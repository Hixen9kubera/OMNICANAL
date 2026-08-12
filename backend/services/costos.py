"""
Motor de precios/costos de Mercado Libre.

Portado del pipeline de referencia (KuberaPipelineV1.0/ml) y validado al 100 %
contra la tabla `costos_finales` existente.

Fórmula del precio sugerido (idéntica a la data guardada):

    precio_sin_iva  = (costo_unitario * (1 + MARGEN) + fee_envio) / (1 - pct)
    precio_sugerido = precio_sin_iva * (1 + IVA)
    costo_comision  = precio_sin_iva * pct
    precio_base      = precio_sugerido / (1 - DESCUENTO)   # precio "tachado"

donde:
    MARGEN = 0.48                 margen de ganancia sobre el costo
    IVA    = 0.16                 IVA MX (este proyecto usa 16 %)
    pct    = pct_comision (decimal, ej. 0.15) de la API listing_prices de ML
    fee_envio = tabla oficial ML (_TARIFA_ML) por peso efectivo, con iteración
    costo_unitario = costo_producto + costo_cbm  (= costos_validados.costo_total)

Cuando un SKU no está en `costos_finales`, se calcula aquí desde `costos_validados`
y se persiste (con log). El recálculo manual actualiza `costos_finales` y deja log.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests

from config import settings
from services import (alertas, core_read, costing_mirror, costing_read,
                      costing_write, db, lecturas_fuente, meli)

log = logging.getLogger("uvicorn.error")

# ── Constantes de la fórmula ────────────────────────────────────────────────────
MARGEN_DEFAULT   = 0.48
IVA_RATE         = 0.16
DESCUENTO_BASE   = 0.16   # precio_base = precio_sugerido / (1 - DESCUENTO_BASE); determinista
PRECIO_REFERENCIA = 100.0


def _comision_categoria_db(cat_id: str) -> float | None:
    """
    Comisión REAL de una categoría desde nuestra data (costos_finales): la más
    frecuente para ese ml_cat_id (se cacheó cuando la API de ML respondía). None
    si nunca costeamos esa categoría → no se inventa un porcentaje.
    """
    if not cat_id:
        return None
    # F6 (corte): con kubera como fuente de escritura, la caché de comisiones
    # también se consulta ahí primero; MySQL (espejo inverso) es el fallback.
    if costing_write.activo():
        try:
            pct = costing_read.pct_comision_categoria(cat_id)
            if pct is not None:
                return pct
        except Exception:  # noqa: BLE001
            pass
    try:
        row = db.fetch_one(
            """SELECT pct_comision FROM costos_finales
               WHERE ml_cat_id=%s AND pct_comision > 0
               GROUP BY pct_comision ORDER BY COUNT(*) DESC LIMIT 1""",
            (cat_id,),
        )
        return float(row["pct_comision"]) if row and row.get("pct_comision") else None
    except Exception:  # noqa: BLE001
        return None

# Tarifa de flete por volumen ($/m³). El costo_cbm histórico se calculaba por
# embarque (flete real ÷ CBM del contenedor); para el recálculo manual usamos una
# tarifa fija de referencia. costo_cbm = volumen_m³ × TARIFA_CBM_M3.
TARIFA_CBM_M3 = 7500.0


def volumen_m3(largo: float, ancho: float, alto: float) -> float:
    """Volumen en m³ a partir de dimensiones en cm (0 si faltan)."""
    if largo and ancho and alto:
        return (float(largo) * float(ancho) * float(alto)) / 1_000_000.0
    return 0.0


def costo_cbm_desde_dims(largo: float, ancho: float, alto: float,
                         tarifa: float = TARIFA_CBM_M3) -> float:
    """Flete por volumen para una pieza: volumen_m³ × tarifa ($/m³)."""
    return round(volumen_m3(largo, ancho, alto) * tarifa, 2)

# ── Config de la cuenta/API de Mercado Libre ────────────────────────────────────
_ML_API               = "https://api.mercadolibre.com"
DEFAULT_ACCOUNT       = "BEKURA"
# gold_pro (Premium): TODO el catálogo se migró a Premium el 2026-07-16 y el
# publicador vendorizado publica gold_pro. Calcular comisiones con gold_special
# subestimaría el fee ~4.5 puntos (ej. 15% vs 19.5%) y el precio sugerido
# saldría con margen de menos.
DEFAULT_LISTING_TYPE  = "gold_pro"
DEFAULT_LOGISTIC      = "xd_drop_off"
DEFAULT_SHIPPING_MODE = "me2"

# ── Tarifas Mercado Envíos México (tabla oficial ML, 2026-07) ───────────────────
# Costo DIRECTO por (tramo de peso × tramo de precio del producto) — ya no es una
# base × factor aproximado, es la tabla real (COSTOSENVIOML.csv). Se usa
# max(peso_real, peso_volumétrico). Columnas de precio:
#   0: $0–98.99   1: $99–198.99   2: $199–298.99
#   3: $299–498.99  4: $499–998.99  5: desde $999
_TARIFA_ML: list[tuple[float, list[float]]] = [
    (0.3,   [25.00,   32.00,   35.00,   52.40,   65.50,   65.50]),
    (0.5,   [28.50,   34.00,   38.00,   56.00,   70.00,   70.00]),
    (1.0,   [33.00,   38.00,   39.00,   59.60,   74.50,   74.50]),
    (2.0,   [35.00,   40.00,   41.00,   67.60,   84.50,   84.50]),
    (3.0,   [37.00,   46.00,   48.00,   76.00,   88.50,   95.00]),
    (4.0,   [39.00,   50.00,   54.00,   82.40,   95.50,   103.00]),
    (5.0,   [40.00,   53.00,   59.00,   88.00,   102.50,  110.00]),
    (7.0,   [45.00,   59.00,   70.00,   98.00,   122.50,  122.50]),
    (9.0,   [51.00,   67.00,   81.00,   111.60,  139.50,  139.50]),
    (12.0,  [59.00,   78.00,   96.00,   129.20,  161.50,  161.50]),
    (15.0,  [69.00,   92.00,   113.00,  152.00,  190.00,  190.00]),
    (20.0,  [81.00,   108.00,  140.00,  178.00,  222.50,  222.50]),
    (30.0,  [102.00,  137.00,  195.00,  225.20,  281.50,  281.50]),
    (40.0,  [126.00,  170.00,  250.00,  279.20,  349.00,  349.00]),
    (50.0,  [163.00,  220.00,  305.00,  361.20,  451.50,  451.50]),
    (60.0,  [183.00,  247.00,  334.00,  405.60,  507.00,  507.00]),
    (70.0,  [188.00,  254.00,  363.00,  416.40,  520.50,  520.50]),
    (80.0,  [196.00,  264.00,  392.00,  433.60,  542.00,  542.00]),
    (90.0,  [220.00,  297.00,  421.00,  487.60,  609.50,  609.50]),
    (100.0, [254.00,  343.00,  450.00,  562.40,  703.00,  703.00]),
    (125.0, [288.00,  389.00,  523.00,  637.20,  796.50,  796.50]),
    (150.0, [382.00,  516.00,  694.00,  846.00,  1057.50, 1057.50]),
    (175.0, [476.00,  643.00,  865.00,  1054.80, 1318.50, 1318.50]),
    (200.0, [570.00,  770.00,  1036.00, 1263.60, 1579.50, 1579.50]),
    (225.0, [664.00,  897.00,  1207.00, 1472.40, 1840.50, 1840.50]),
    (250.0, [758.00,  1024.00, 1378.00, 1681.20, 2101.50, 2101.50]),
    (275.0, [852.00,  1151.00, 1549.00, 1890.00, 2362.50, 2362.50]),
    (300.0, [946.00,  1278.00, 1720.00, 2098.40, 2623.00, 2623.00]),
    (325.0, [1040.00, 1406.00, 1892.00, 2308.00, 2885.00, 2885.00]),
    (350.0, [1134.00, 1533.00, 2063.00, 2516.80, 3146.00, 3146.00]),
    # Más de 350 kg: la tabla oficial repite la fila de 325–350 (sin tramo propio).
]

# Tope superior de cada tramo de precio, en el mismo orden que las columnas arriba.
_TRAMOS_PRECIO = [98.99, 198.99, 298.99, 498.99, 998.99]


def _fila_tarifa_ml(peso_kg: float) -> list[float]:
    for limite, costos_fila in _TARIFA_ML:
        if peso_kg <= limite:
            return costos_fila
    return _TARIFA_ML[-1][1]  # >350 kg → misma fila que 325–350


def _columna_precio_ml(precio: float) -> int:
    for i, tope in enumerate(_TRAMOS_PRECIO):
        if precio <= tope:
            return i
    return len(_TRAMOS_PRECIO)  # última columna: "Desde $999"


def calc_fee_envio_ml(peso_kg: float, precio: float) -> float:
    """
    Fee de envío ML: lookup DIRECTO en la tabla oficial (peso efectivo × tramo de
    precio del producto) — sin aproximaciones. Como la columna depende del precio
    y el precio depende del fee, se resuelve por iteración en `calcular_pricing`.
    """
    fila = _fila_tarifa_ml(peso_kg)
    col = _columna_precio_ml(precio)
    return round(fila[col], 2)


def calc_precio_sugerido(costo: float, pct: float, fee_envio: float,
                         margen: float = MARGEN_DEFAULT,
                         iva: float = IVA_RATE) -> float:
    """precio = (costo*(1+margen) + fee_envio) / (1 - pct) * (1 + iva). pct en decimal."""
    numerador = costo * (1.0 + margen) + fee_envio
    precio_sin_iva = numerador / (1.0 - pct)
    return round(precio_sin_iva * (1.0 + iva), 2)


# ── pct_comisión desde la API listing_prices de ML ──────────────────────────────

def pct_comision_ml(cat_id: str, dims_str: str = "",
                    cuenta: str = DEFAULT_ACCOUNT,
                    _reintentado: bool = False) -> float | None:
    """
    Devuelve el % de comisión de ML (DECIMAL, ej. 0.15) para una categoría.
    Una sola llamada a /sites/MLM/listing_prices con precio de referencia $100
    (la comisión es fija por categoría/tipo de publicación). None si falla.
    """
    if not cat_id:
        return None
    token = meli._access_token(cuenta) or meli._access_token()
    if not token:
        log.warning("pct_comision_ml: sin token ML")
        return None
    params = {
        "price": PRECIO_REFERENCIA,
        "category_id": cat_id,
        "listing_type_id": DEFAULT_LISTING_TYPE,
        "logistic_type": DEFAULT_LOGISTIC,
        "shipping_mode": DEFAULT_SHIPPING_MODE,
    }
    if dims_str:
        params["dimensions"] = dims_str
    try:
        r = requests.get(f"{_ML_API}/sites/MLM/listing_prices",
                         headers={"Authorization": f"Bearer {token}"},
                         params=params, timeout=30)
    except Exception as exc:  # noqa: BLE001
        log.warning("listing_prices error de red: %s", exc)
        return None
    if r.status_code == 200:
        fd = (r.json().get("sale_fee_details") or {})
        pct = fd.get("percentage_fee")
        return round(float(pct) / 100.0, 4) if pct is not None else None
    if r.status_code == 401 and not _reintentado:
        nuevo = meli.refrescar_token(cuenta)
        if nuevo:
            return pct_comision_ml(cat_id, dims_str, cuenta, _reintentado=True)
    log.warning("listing_prices %s: %s", r.status_code, r.text[:150])
    return None


# ── Orquestador: precio a partir del costo + categoría ──────────────────────────

def _peso_efectivo(peso_kg: float, largo: float, ancho: float, alto: float) -> tuple[float, str]:
    peso_real = peso_kg or 0.5
    peso_g = int(round(peso_real * 1000))
    if largo > 0 and ancho > 0 and alto > 0:
        dims_str = f"{alto:.1f}x{ancho:.1f}x{largo:.1f},{peso_g}"
        peso_vol = (largo * ancho * alto) / 5000.0
    else:
        dims_str = f"10.0x10.0x10.0,{peso_g}"
        peso_vol = 0.0
    return max(peso_real, peso_vol), dims_str


def calcular_pricing(costo_unitario: float, cat_id: str,
                     peso_kg: float = 0.0, largo: float = 0.0,
                     ancho: float = 0.0, alto: float = 0.0,
                     cuenta: str = DEFAULT_ACCOUNT,
                     incluir_envio: bool = True,
                     margen: float = MARGEN_DEFAULT,
                     pct_override: float | None = None) -> dict[str, Any] | None:
    """
    Calcula precio_sugerido/precio_base y el desglose para un costo + categoría.
    La comisión se toma, en orden: pct_override (manual) → API de ML → fallback
    COMISION_FALLBACK (marca comision_estimada=True). Así el cálculo NO se bloquea
    si el token de ML no está disponible.
    """
    peso_efectivo, dims_str = _peso_efectivo(peso_kg, largo, ancho, alto)
    # Comisión, en orden: manual → API ML (token) → comisión REAL por categoría
    # cacheada en costos_finales. NADA de porcentaje fijo inventado.
    estimada = False
    if pct_override is not None and pct_override > 0:
        pct = float(pct_override)
    else:
        pct = pct_comision_ml(cat_id, dims_str, cuenta)
        if pct is None:  # sin token → comisión histórica de ESA categoría
            pct = _comision_categoria_db(cat_id)
            estimada = pct is not None  # vino de nuestra data, no de ML en vivo
        if pct is None:
            return None  # sin comisión confiable → el usuario la ingresa manual

    if incluir_envio:
        fee_envio = calc_fee_envio_ml(peso_efectivo, 400.0)
        for _ in range(8):
            precio_iter = calc_precio_sugerido(costo_unitario, pct, fee_envio, margen)
            fee_nuevo = calc_fee_envio_ml(peso_efectivo, precio_iter)
            if fee_nuevo == fee_envio:
                break
            fee_envio = fee_nuevo
    else:
        fee_envio = 0.0

    precio_sug = calc_precio_sugerido(costo_unitario, pct, fee_envio, margen)
    precio_base = round(precio_sug / (1 - DESCUENTO_BASE), 2)

    precio_sin_iva = precio_sug / (1.0 + IVA_RATE)
    costo_comision = round(precio_sin_iva * pct, 2)
    iva_mnt = round(precio_sug - precio_sin_iva, 2)
    ganancia_neta = round(precio_sug - costo_comision - fee_envio - iva_mnt - costo_unitario, 2)
    roi = round(ganancia_neta / costo_unitario, 4) if costo_unitario else 0.0

    return {
        "pct_comision": pct,
        "comision_estimada": estimada,
        "costo_comision": costo_comision,
        "costo_fee_envio": fee_envio,
        "iva_mnt": iva_mnt,
        "precio_sugerido": precio_sug,
        "precio_base": precio_base,
        "descuento_pct": DESCUENTO_BASE,
        "ganancia_neta": ganancia_neta,
        "roi": roi,
    }


def aplicar_precio_manual(pricing: dict[str, Any], base: dict[str, Any],
                          overrides: dict[str, Any] | None,
                          incluir_envio: bool = True) -> dict[str, Any]:
    """
    Un precio ESCRITO A MANO en el Estudio MANDA sobre el derivado del costo.

    El precio normalmente se deriva (costo → margen → comisión → envío), pero el
    panel permite fijarlo. Cuando eso pasa NO basta con cambiar el número: hay
    que rehacer el desglose HACIA ATRÁS (comisión, IVA, ganancia, ROI) o la
    pantalla mostraría la utilidad del precio calculado, no la del real. El fee
    de envío se re-evalúa porque en ML depende del precio.

    Basta con dar uno de los dos: el otro sale de la misma relación
    DESCUENTO_BASE que usa el cálculo normal.
    """
    def _pos(v: Any) -> float | None:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f > 0 else None

    pb = _pos((overrides or {}).get("precio_base"))
    ps = _pos((overrides or {}).get("precio_sugerido"))
    if pb is None and ps is None:
        return pricing
    if ps is None:
        ps = round(pb * (1 - DESCUENTO_BASE), 2)
    if pb is None:
        pb = round(ps / (1 - DESCUENTO_BASE), 2)

    costo_unitario = float(base.get("costo_unitario") or 0)
    fee_envio = pricing["costo_fee_envio"]
    if incluir_envio:
        peso_efectivo, _ = _peso_efectivo(
            base.get("peso") or 0, base.get("largo") or 0,
            base.get("ancho") or 0, base.get("alto") or 0)
        fee_envio = calc_fee_envio_ml(peso_efectivo, ps)
    precio_sin_iva = ps / (1.0 + IVA_RATE)
    costo_comision = round(precio_sin_iva * pricing["pct_comision"], 2)
    iva_mnt = round(ps - precio_sin_iva, 2)
    ganancia_neta = round(ps - costo_comision - fee_envio - iva_mnt - costo_unitario, 2)
    return {
        **pricing,
        "precio_sugerido": ps,
        "precio_base": pb,
        "costo_fee_envio": fee_envio,
        "costo_comision": costo_comision,
        "iva_mnt": iva_mnt,
        "ganancia_neta": ganancia_neta,
        "roi": round(ganancia_neta / costo_unitario, 4) if costo_unitario else 0.0,
        "precio_manual": True,
    }


# ── Lectura de costos base desde costos_validados ───────────────────────────────

def costo_desde_validados(sku: str) -> dict[str, Any] | None:
    """
    Costo base + dimensiones de un SKU desde costos_validados.
    costo_unitario = costo_total (o costo_producto + costo_cbm si falta). None si no existe.
    """
    cv = None
    # PASO 3 (12-ago-2026): kubera es la ÚNICA fuente. El comentario viejo decía
    # que el espejo inverso mantenía MySQL fresco — ya no: `costos_validados`
    # quedó congelada al retirar el espejo, así que caer ahí devolvería el costo
    # de antes del último recálculo. Este valor alimenta precios: un costo viejo
    # sale caro y en silencio.
    if costing_write.activo():
        cv = costing_read.validados(sku)
        lecturas_fuente.anotar("costing", "kubera")
    if cv is None:
        cv = db.fetch_one("SELECT * FROM costos_validados WHERE sku=%s", (sku,))
    if not cv:
        return None
    def _f(v: Any) -> float:
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0
    costo_prod = _f(cv.get("costo_producto"))
    costo_cbm = _f(cv.get("costo_cbm"))
    costo_total = _f(cv.get("costo_total")) or round(costo_prod + costo_cbm, 2)
    return {
        "costo_producto": costo_prod,
        "costo_cbm": costo_cbm,
        "costo_unitario": costo_total,
        "largo": _f(cv.get("largo")),
        "ancho": _f(cv.get("ancho")),
        "alto": _f(cv.get("alto")),
        "peso": _f(cv.get("peso")),
    }


# ── Persistencia + logs ─────────────────────────────────────────────────────────

def _log_costo(sku: str, accion: str, origen: str, detalle: dict[str, Any]) -> None:
    def _mysql() -> None:
        try:
            db.execute(
                "INSERT INTO costos_logs (sku, accion, origen, detalle) VALUES (%s,%s,%s,%s)",
                (sku, accion, origen, json.dumps(detalle, ensure_ascii=False, default=str)),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("no se pudo escribir costos_logs(%s): %s", sku, exc)

    # F6 (corte): la bitácora primaria es ops.process_log; costos_logs queda de
    # espejo inverso (el panel sigue leyéndola completa durante la transición).
    if costing_write.activo():
        costing_write.registrar_log(sku, accion, origen, detalle, _mysql)
        return
    _mysql()
    # Dual-write F3 (flag SUPABASE_DUAL_WRITE): espejo a ops.process_log en un
    # hilo aparte — nunca en el camino crítico, nunca rompe la operación.
    costing_mirror.en_hilo(costing_mirror.espejar_log, sku, accion, origen, detalle)


def _guardar_finales(sku: str, base: dict[str, Any], pricing: dict[str, Any],
                     cat_id: str) -> dict[str, Any]:
    """UPSERT en costos_finales con el costo base + el pricing calculado."""
    fila = {
        "sku": sku,
        "costo_producto": base.get("costo_producto"),
        "costo_cbm": base.get("costo_cbm"),
        "costo_unitario": base.get("costo_unitario"),
        "costo_comision": pricing["costo_comision"],
        "costo_fee_envio": pricing["costo_fee_envio"],
        "precio_sugerido": pricing["precio_sugerido"],
        "precio_base": pricing["precio_base"],
        "largo": base.get("largo"), "alto": base.get("alto"), "ancho": base.get("ancho"),
        "peso": base.get("peso"),
        "ml_cat_id": cat_id or None,
        "pct_comision": pricing["pct_comision"],
        "peso_origen": "costos_validados",
    }
    cols = ", ".join(fila.keys())
    ph = ", ".join(["%s"] * len(fila))
    upd = ", ".join(f"{k}=VALUES({k})" for k in fila if k != "sku")

    def _mysql() -> None:
        db.execute(
            f"INSERT INTO costos_finales ({cols}) VALUES ({ph}) "
            f"ON DUPLICATE KEY UPDATE {upd}, updated_at=NOW()",
            tuple(fila.values()),
        )

    # F6 (corte, opción A): kubera primaria + MySQL de espejo inverso.
    if costing_write.activo():
        costing_write.guardar_finales(sku, dict(fila), _mysql)
        return fila
    _mysql()
    # Dual-write F3: espejo a costing.costos_finales (MySQL sigue siendo la fuente).
    costing_mirror.en_hilo(costing_mirror.espejar_finales, sku, dict(fila))
    return fila


def _guardar_validados(sku: str, base: dict[str, Any]) -> None:
    """
    UPSERT del costo base editado en costos_validados (dims/peso/costo_producto/
    costo_cbm/costo_total). Sólo toca esas columnas: contenedor/cajas/etc. de una
    fila existente se conservan (ON DUPLICATE KEY UPDATE por columna).
    """
    fila = {
        "sku": sku,
        "largo": base.get("largo"), "alto": base.get("alto"), "ancho": base.get("ancho"),
        "peso": base.get("peso"),
        "costo_producto": base.get("costo_producto"),
        "costo_cbm": base.get("costo_cbm"),
        "costo_total": base.get("costo_unitario"),
    }
    cols = ", ".join(fila.keys())
    ph = ", ".join(["%s"] * len(fila))
    upd = ", ".join(f"{k}=VALUES({k})" for k in fila if k != "sku")

    def _mysql() -> None:
        db.execute(
            f"INSERT INTO costos_validados ({cols}) VALUES ({ph}) "
            f"ON DUPLICATE KEY UPDATE {upd}",
            tuple(fila.values()),
        )

    # La fila kubera: solo columnas tocadas aquí; el costo_total del espejo =
    # costo_unitario, igual que la fila de MySQL.
    fila_kb = {**{k: fila.get(k) for k in ("largo", "alto", "ancho", "peso",
                                           "costo_producto", "costo_cbm")},
               "costo_total": fila.get("costo_total")}
    # F6 (corte, opción A): kubera primaria + MySQL de espejo inverso.
    if costing_write.activo():
        costing_write.guardar_validados(sku, fila_kb, _mysql)
        return
    _mysql()
    # Dual-write F3: espejo a costing.costos_validados.
    costing_mirror.en_hilo(costing_mirror.espejar_validados, sku, fila_kb)


def _preparar_base(sku: str, overrides: dict[str, Any] | None,
                   auto_cbm: bool) -> tuple[dict[str, Any], str]:
    """
    Arma el costo base (dims/peso/costo_producto/costo_cbm) desde costos_validados,
    con semilla de costos_finales, aplica overrides y resuelve costo_unitario.
    Si auto_cbm y no vino costo_cbm explícito, lo deriva de las dims (× tarifa).
    Devuelve (base, cat_id).
    """
    base = costo_desde_validados(sku) or {}
    cf = None
    # F6 (corte): la semilla de costos_finales sale de kubera. OJO modelo v4:
    # costing.costos_finales NO lleva dims — si a validados le faltan, las dims
    # se complementan del MySQL espejo (solo durante la transición; al retirar
    # MySQL, las dims solo vivirán en costos_validados, que es el contrato v4).
    if costing_write.activo():
        try:
            cf = costing_read.finales(sku)  # None (sin fila) → reconsulta MySQL
        except Exception as exc:  # noqa: BLE001
            lecturas_fuente.anotar("costing", "fallback", str(exc))
            log.warning("lectura kubera falló (finales %s) — fallback MySQL: %s", sku, exc)
            cf = None
        if cf is not None and not all(base.get(k) for k in ("largo", "alto", "ancho", "peso")):
            cf = dict(cf)
            try:
                cf_my = db.fetch_one(
                    "SELECT largo, alto, ancho, peso FROM costos_finales WHERE sku=%s",
                    (sku,)) or {}
                for k in ("largo", "alto", "ancho", "peso"):
                    if cf.get(k) is None and cf_my.get(k) is not None:
                        cf[k] = cf_my[k]
            except Exception:  # noqa: BLE001 — sin dims de MySQL, la kubera basta
                pass
    if cf is None:
        cf = db.fetch_one("SELECT * FROM costos_finales WHERE sku=%s", (sku,)) or {}
    for k in ("costo_producto", "costo_cbm", "largo", "alto", "ancho", "peso"):
        if not base.get(k) and cf.get(k) is not None:
            try:
                base[k] = float(cf[k])
            except (TypeError, ValueError):
                pass
    cbm_manual = False
    if overrides:
        for k, v in overrides.items():
            if k in ("costo_producto", "costo_cbm", "largo", "alto", "ancho", "peso"):
                try:
                    base[k] = float(v)
                    if k == "costo_cbm":
                        cbm_manual = True
                except (TypeError, ValueError):
                    pass
    if auto_cbm and not cbm_manual:
        base["costo_cbm"] = costo_cbm_desde_dims(
            base.get("largo") or 0, base.get("ancho") or 0, base.get("alto") or 0)
    base["costo_unitario"] = round(
        float(base.get("costo_producto") or 0) + float(base.get("costo_cbm") or 0), 2)
    # Costo TOTAL escrito a mano (campo "Costo" del Estudio): lo que se teclea es
    # el costo, punto. Se reparte como producto=lo tecleado y flete=0 para que el
    # total mostrado sea EXACTAMENTE ese — si además se sumara un CBM derivado de
    # las dims, el número guardado no coincidiría con el escrito.
    # Para desglosar producto vs flete está el bloque COSTOS (costo USD + dims).
    try:
        cu = float((overrides or {}).get("costo_unitario") or 0)
    except (TypeError, ValueError):
        cu = 0.0
    if cu > 0:
        base["costo_producto"] = cu
        base["costo_cbm"] = 0.0
        base["costo_unitario"] = round(cu, 2)
    cat = (overrides or {}).get("ml_cat_id") or cf.get("ml_cat_id") or _resolver_cat_ml(sku)
    return base, cat


def _cat_ml_kubera(sku: str) -> str:
    """
    Categoría ML del mapa `channel.product_category` de la BD kubera — el mismo
    que usa el panel de Análisis para agrupar por categoría. Su fuente `panel`
    es la elección humana, que manda sobre cualquier detector (regla de la casa).
    """
    try:
        from services import supabase_db as sdb
        if not sdb.disponible():
            return ""
        row = sdb.fetch_one(
            "select category_id from channel.product_category "
            " where sku = %s::citext and channel_id = 'mercado_libre' limit 1",
            (sku,))
        return str(row["category_id"]) if row and row.get("category_id") else ""
    except Exception:  # noqa: BLE001
        return ""


def _cat_ml_de(sku: str) -> str:
    """Categoría ML de UN SKU: categorias_ml → postmeta de Woo → mapa kubera."""
    if not sku:
        return ""
    try:
        row = db.fetch_one("SELECT category_id FROM categorias_ml WHERE sku=%s", (sku,))
        if row and row.get("category_id"):
            return str(row["category_id"])
    except Exception:  # noqa: BLE001
        pass
    try:
        wc = None
        # PASO 3 del desmantelamiento (12-ago-2026): wc_id sale de core.products
        # y ya NO se reconsulta MySQL — ver la nota larga en
        # pedidos_ml.resolver_producto. Sin wc_id se sigue al mapa de kubera del
        # final, que es el que manda para la categoría.
        if settings.supabase_read_core:
            try:
                wc = core_read.wc_id_de_sku(sku)
                lecturas_fuente.anotar("core", "kubera")
            except Exception as exc:  # noqa: BLE001
                lecturas_fuente.anotar("core", "fallback", str(exc))
                alertas.avisar("lectura_fallback:core",
                               f"⚠️ Lectura de CORE falló (categoria_ml), se "
                               f"resuelve por el mapa de kubera: {exc}")
                log.warning("lectura kubera falló (categoria_ml) — sigue a kubera: %s", exc)
        if wc:
            from services import wp_db
            if wp_db.disponible():
                m = wp_db.postmeta(int(wc), ["ml_category_id"])
                if m.get("ml_category_id"):
                    return str(m["ml_category_id"])
    except Exception:  # noqa: BLE001
        pass
    return _cat_ml_kubera(sku)


def _resolver_cat_ml(sku: str) -> str:
    """
    Busca la categoría ML del SKU cuando no viene en overrides ni en costos_finales:
      1) tabla categorias_ml (nuestra DB)
      2) postmeta ml_category_id de WooCommerce (vía wc_id de productos)
      3) mapa channel.product_category de la BD kubera

    Si el SKU es una VARIANTE sin categoría propia, HEREDA la del padre: las
    variantes de un producto viven en la misma categoría de ML. El padre se
    resuelve por la estructura de WooCommerce (post_parent), no por el nombre
    del SKU. Sin esta herencia el costeo se cae con 422 en cuanto una variante
    no tiene categoría: sin categoría no hay comisión, y sin comisión no hay
    precio que guardar (caso real: CAM-0030-IND/-QUE, colchones por talla).

    Devuelve "" si no se encuentra.
    """
    cat = _cat_ml_de(sku)
    if cat:
        return cat
    try:
        from services import wp_db
        padre = wp_db.sku_padre(sku)
    except Exception:  # noqa: BLE001
        padre = ""
    return _cat_ml_de(padre) if padre and padre != sku else ""


def computar(sku: str, overrides: dict[str, Any] | None = None,
             incluir_envio: bool = True, margen: float = MARGEN_DEFAULT,
             cuenta: str = DEFAULT_ACCOUNT, auto_cbm: bool = False) -> dict[str, Any] | None:
    """
    Calcula costo + precio SIN persistir (para la vista previa del tab Costos).
    Devuelve un dict plano con el costo base, el volumen y todo el pricing, o None
    solo si falta el costo base. La comisión sale de pct_comision (override) →
    API ML → fallback; sin categoría ni token igual calcula (comision_estimada).
    """
    base, cat = _preparar_base(sku, overrides, auto_cbm)
    if base.get("costo_unitario", 0) <= 0:
        return None  # sin costo base no hay nada que calcular
    pct_override = None
    try:
        pv = (overrides or {}).get("pct_comision")
        pct_override = float(pv) if pv not in (None, "") else None
    except (TypeError, ValueError):
        pct_override = None
    pricing = calcular_pricing(
        base["costo_unitario"], cat,
        peso_kg=base.get("peso", 0), largo=base.get("largo", 0),
        ancho=base.get("ancho", 0), alto=base.get("alto", 0),
        cuenta=cuenta, incluir_envio=incluir_envio, margen=margen,
        pct_override=pct_override,
    )
    if not pricing:
        return None
    # Un precio fijado a mano en el Estudio pisa al calculado (y rehace el desglose).
    pricing = aplicar_precio_manual(pricing, base, overrides, incluir_envio)
    return {
        "sku": sku,
        "costo_producto": base.get("costo_producto"),
        "costo_cbm": base.get("costo_cbm"),
        "costo_unitario": base["costo_unitario"],
        "largo": base.get("largo"), "alto": base.get("alto"), "ancho": base.get("ancho"),
        "peso": base.get("peso"),
        "volumen_m3": round(volumen_m3(
            base.get("largo") or 0, base.get("ancho") or 0, base.get("alto") or 0), 4),
        "ml_cat_id": cat,
        "margen": margen,
        "incluir_envio": incluir_envio,
        "tarifa_cbm_m3": TARIFA_CBM_M3,
        **pricing,
    }


def asegurar_finales(sku: str, cat_id: str = "",
                     cuenta: str = DEFAULT_ACCOUNT) -> dict[str, Any] | None:
    """
    Garantiza que el SKU tenga precio/costo en costos_finales.
      · Si ya está → lo devuelve tal cual (sin recalcular).
      · Si no → lo calcula desde costos_validados + categoría ML, lo persiste y logea.
    Devuelve el dict con precio_base/precio_sugerido/costo_* o None si no se pudo.
    """
    cf = db.fetch_one("SELECT * FROM costos_finales WHERE sku=%s", (sku,))
    if cf and cf.get("precio_sugerido"):
        return cf

    base = costo_desde_validados(sku)
    if not base or base["costo_unitario"] <= 0:
        log.info("asegurar_finales(%s): sin costo en costos_validados", sku)
        return cf  # puede ser None; no hay con qué calcular
    cat = cat_id or (cf or {}).get("ml_cat_id") or ""
    if not cat:
        log.info("asegurar_finales(%s): sin categoría ML para calcular comisión", sku)
        return cf

    pricing = calcular_pricing(
        base["costo_unitario"], cat,
        peso_kg=base["peso"], largo=base["largo"],
        ancho=base["ancho"], alto=base["alto"], cuenta=cuenta,
    )
    if not pricing:
        return cf

    fila = _guardar_finales(sku, base, pricing, cat)
    _log_costo(sku, "auto", "crear_producto",
               {"base": base, "pricing": pricing, "cat_id": cat})
    log.info("asegurar_finales(%s): calculado psug=%s pbase=%s",
             sku, pricing["precio_sugerido"], pricing["precio_base"])
    return fila


def recalcular(sku: str, overrides: dict[str, Any] | None = None,
               incluir_envio: bool = True, margen: float = MARGEN_DEFAULT,
               cuenta: str = DEFAULT_ACCOUNT, auto_cbm: bool = False) -> dict[str, Any] | None:
    """
    Recálculo MANUAL: toma el costo/dims actuales, aplica overrides editables
    (costo_producto, costo_cbm, largo, alto, ancho, peso), recalcula el precio y
    PERSISTE en costos_validados + costos_finales, dejando log. Devuelve la fila
    guardada (costos_finales) o None. Si auto_cbm, el costo_cbm se deriva de las
    dims (× tarifa) salvo que venga explícito en overrides.
    """
    calc = computar(sku, overrides, incluir_envio, margen, cuenta, auto_cbm)
    if not calc:
        # `computar` devuelve None por DOS motivos distintos y no dan lo mismo:
        #   · sin costo base      → no hay nada que guardar
        #   · sin comisión (casi   → el COSTO sí existe y se puede registrar;
        #     siempre: sin categoría   lo único que no se puede es DERIVAR el precio
        #     ML asignada)             (y aquí no se inventa un % — regla de la casa)
        # Antes ambos casos se perdían igual, así que capturar el costo de un
        # producto recién creado era imposible hasta asignarle categoría. Ahora
        # el costo se guarda y el llamador avisa que faltó el precio.
        base, cat = _preparar_base(sku, overrides, auto_cbm)
        if base.get("costo_unitario", 0) <= 0:
            return None
        _guardar_validados(sku, base)
        _log_costo(sku, "manual", "recalculo_sin_precio",
                   {"overrides": overrides or {}, "base": base, "cat_id": cat})
        log.info("recalcular(%s): costo guardado sin precio (cat_id=%r)", sku, cat)
        return {**base, "sku": sku, "ml_cat_id": cat, "sin_precio": True,
                "motivo_sin_precio": ("el producto no tiene categoría ML asignada"
                                      if not cat else
                                      "no se encontró la comisión de la categoría")}
    base = {k: calc.get(k) for k in
            ("costo_producto", "costo_cbm", "costo_unitario", "largo", "alto", "ancho", "peso")}
    cat = calc["ml_cat_id"]
    pricing = {k: calc[k] for k in
               ("pct_comision", "costo_comision", "costo_fee_envio",
                "precio_sugerido", "precio_base")}

    _guardar_validados(sku, base)
    fila = _guardar_finales(sku, base, pricing, cat)
    _log_costo(sku, "manual", "recalculo",
               {"overrides": overrides or {}, "incluir_envio": incluir_envio,
                "margen": margen, "auto_cbm": auto_cbm, "base": base, "pricing": pricing})
    return fila
