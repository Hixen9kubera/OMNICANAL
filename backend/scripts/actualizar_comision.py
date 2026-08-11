"""
actualizar_comision.py — Corrige SOLO el % de comisión de ML en costos_finales.

POR QUÉ EXISTE
El 99% del catálogo (4,100 de 4,127 SKUs con precio) tiene la comisión escrita
por un único lote del 2026-07-24, con promedio 13.13%. Lo que ML cobró de verdad
en los últimos 60 días fue 16.19% — $92,892 de diferencia sobre $3.7M de venta.
Todo lo recalculado individualmente desde agosto sale en 17–19%, que sí coincide
con lo cobrado: el motor está bien, lo que está mal es el dato guardado.

POR QUÉ NO SE USA "Regenerar costo" DEL PANEL
Ese camino manda auto_cbm=true y sincronizar_woo=true. Rederiva el flete del
contenedor desde las dimensiones (en MUE-0163-TEL lo bajaría de $179.08 a $91.80,
moviendo el costo unitario de $272.76 a $185.48), recalcula los precios y los
EMPUJA a WooCommerce, pisando los que alguien puso a mano. Este script toca dos
columnas y no habla con Woo.

QUÉ TOCA      costos_finales.pct_comision
              costos_finales.costo_comision
              (+ precio_sugerido y precio_base SOLO si se pasa --con-precio)

QUÉ NO TOCA   costo_producto · costo_cbm · costo_unitario · costo_fee_envio
              largo/alto/ancho/peso · contenedor/cajas · costos_validados entera
              WooCommerce — este módulo ni siquiera importa el cliente de Woo.

FUENTE DEL NUEVO PORCENTAJE, en orden
  1. API de ML: /sites/MLM/listing_prices con listing_type gold_pro (Premium).
     Es la buena, y cubre todo el catálogo tenga ventas o no.
  2. Promedio REAL medido en nuestros propios pedidos, agrupado por categoría ML,
     exigiendo al menos --min-ventas líneas. Solo si la API no contesta.
  3. Si ninguna resuelve, el SKU se salta. Nunca se inventa un número.

Un porcentaje fuera de [PCT_MIN, PCT_MAX] se descarta por absurdo: es un error de
lectura, no una comisión. Ese filtro es justamente el que faltó el 24-jul, cuando
quedaron 316 SKUs con 0.0000 guardado y marcado como cobro real.

ESCRITURA
Reusa costing_write.guardar_finales — el camino F6 (kubera primaria + espejo
inverso a MySQL), con su cola de reproceso y sus alertas. El lado MySQL va como
UPDATE de columnas puntuales, NO como upsert de fila completa, para que ninguna
columna ajena pueda quedar en NULL por accidente.

USO
  # ver qué haría, sin escribir nada (comportamiento por defecto)
  backend/.venv/Scripts/python.exe backend/scripts/actualizar_comision.py

  # un solo SKU, para inspeccionar antes de nada
  backend/.venv/Scripts/python.exe backend/scripts/actualizar_comision.py --sku MUE-0163-TEL

  # aplicar de verdad (exige nombrar el destino a mano)
  backend/.venv/Scripts/python.exe backend/scripts/actualizar_comision.py --real --acepto-destino tukwcvsi
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from config import settings  # noqa: E402
from services import costing_write, costos, db  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

# La consola de Windows llega en cp1252 y revienta con las flechas y guiones
# largos del reporte. Se fuerza UTF-8 en la salida, no en el texto.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ORIGEN = "script:actualizar_comision"
ACCION = "comision"

# Guardas de cordura. ML México cobra entre 10% y 19.5% según categoría y tipo de
# publicación; en 12,461 líneas de pedido reales medimos de 3.33% a 20% (los
# extremos son promociones de ML). Fuera de este rango no es una comisión.
PCT_MIN = 0.05
PCT_MAX = 0.25

# Columnas que upsert_finales escribe en kubera. Se listan aquí porque ese upsert
# NO es parcial: hace fila.get(k) para cada una y lo que falte se guarda como
# NULL. Por eso siempre se parte de la fila actual y solo se sustituye lo nuestro.
COLS_KUBERA = ("costo_producto", "costo_cbm", "costo_unitario", "costo_comision",
               "costo_fee_envio", "precio_sugerido", "precio_base", "ml_cat_id",
               "pct_comision", "peso_origen")


# ── Fuentes del porcentaje ────────────────────────────────────────────────────

def tasas_medidas(min_ventas: int) -> dict[str, dict[str, Any]]:
    """
    Comisión REAL por categoría ML, medida en nuestros propios pedidos.

    Es el respaldo cuando la API de ML no contesta. Se pondera por línea y se
    guarda la desviación para poder descartar categorías inestables: dentro de
    una misma categoría la tasa casi no se mueve (desviación típica < 1 punto),
    así que una desviación alta significa que la categoría mezcla cosas
    distintas y su promedio no sirve para pronosticar.
    """
    sql = """
        select cf.ml_cat_id                                                as cat,
               avg(i.comision / nullif(i.precio_unitario * i.cantidad, 0)) as tasa,
               stddev(i.comision / nullif(i.precio_unitario * i.cantidad, 0)) as sd,
               count(*)                                                   as n
          from channel.order_items i
          join costing.costos_finales cf
            on cf.sku = i.sku and cf.canal = i.canal
         where i.canal = 'mercado_libre'
           and i.comision > 0
           and i.precio_unitario > 0
           and cf.ml_cat_id is not null
         group by 1
        having count(*) >= %s
    """
    with sdb.get_cursor() as cur:
        cur.execute(sql, (min_ventas,))
        return {r["cat"]: {"tasa": float(r["tasa"]),
                           "sd": float(r["sd"] or 0),
                           "n": int(r["n"])}
                for r in cur.fetchall()}


# La comisión de ML es por CATEGORÍA, no por SKU: preguntar una vez por SKU
# serían 4,127 llamadas HTTP para ~300 respuestas distintas, además del riesgo
# de que ML nos limite por volumen. Se cachea por categoría durante la corrida.
_CACHE_API: dict[str, float | None] = {}


def pct_desde_api(cat_id: str, cuenta: str) -> float | None:
    """El porcentaje que ML declara hoy para la categoría, en Premium."""
    if not cat_id:
        return None
    if cat_id in _CACHE_API:
        return _CACHE_API[cat_id]
    try:
        pct = costos.pct_comision_ml(cat_id, cuenta=cuenta)
    except Exception as exc:  # noqa: BLE001 — una categoría que falla no aborta el lote
        print(f"    ! API ML falló para {cat_id}: {str(exc)[:90]}")
        pct = None
    _CACHE_API[cat_id] = pct
    return pct


def resolver_pct(fila: dict, tasas: dict[str, dict], fuente: str,
                 cuenta: str, sd_max: float) -> tuple[float | None, str]:
    """(pct, de_dónde_salió). None cuando ninguna fuente da un número creíble."""
    cat = fila.get("ml_cat_id")

    if fuente in ("api", "ambas"):
        pct = pct_desde_api(cat, cuenta)
        if pct is not None and PCT_MIN <= pct <= PCT_MAX:
            return pct, "api"

    if fuente in ("ventas", "ambas"):
        m = tasas.get(cat)
        if m and m["sd"] <= sd_max and PCT_MIN <= m["tasa"] <= PCT_MAX:
            return round(m["tasa"], 4), f"ventas({m['n']})"

    return None, "sin fuente"


# ── Construcción de la fila ───────────────────────────────────────────────────

def construir(fila: dict, pct: float, con_precio: bool) -> dict[str, Any]:
    """
    La fila COMPLETA que espera upsert_finales, con la comisión sustituida.

    costo_comision se rehace SIEMPRE, porque es la comisión expresada en pesos
    sobre el precio: dejarlo con el valor viejo mientras el porcentaje cambia
    dejaría la fila contradiciéndose a sí misma.

    precio_sugerido solo se mueve con --con-precio, y aun entonces se recalcula
    con el costo unitario y el envío YA GUARDADOS. Nunca se re-deriva el CBM
    desde las dimensiones ni se vuelve a consultar la tarifa de envío: lo único
    que cambia es el divisor (1 - pct) de la fórmula.
    """
    nueva = {k: fila.get(k) for k in COLS_KUBERA}
    # Se redondea ANTES de usarlo: si costo_comision se calculara con el pct sin
    # redondear, los dos números guardados no se corresponderían entre sí y nadie
    # podría reconstruir uno desde el otro.
    pct = round(pct, 4)
    nueva["pct_comision"] = pct

    ps = float(fila.get("precio_sugerido") or 0)
    cu = float(fila.get("costo_unitario") or 0)
    fee = float(fila.get("costo_fee_envio") or 0)

    if con_precio and cu > 0:
        ps = costos.calc_precio_sugerido(cu, pct, fee)
        nueva["precio_sugerido"] = ps
        nueva["precio_base"] = round(ps / (1 - costos.DESCUENTO_BASE), 2)

    # Misma fórmula que calcular_pricing: la comisión se cobra sobre el precio
    # sin IVA, no sobre el precio de lista.
    nueva["costo_comision"] = round(ps / (1.0 + costos.IVA_RATE) * pct, 2)
    return nueva


def escribir(sku: str, nueva: dict[str, Any], con_precio: bool, origen_pct: str) -> None:
    """Escribe por el camino F6: kubera primaria, MySQL de espejo inverso."""
    campos = ["pct_comision", "costo_comision"]
    if con_precio:
        campos += ["precio_sugerido", "precio_base"]

    def _mysql() -> None:
        sets = ", ".join(f"{c}=%s" for c in campos)
        db.execute(f"UPDATE costos_finales SET {sets} WHERE sku=%s",
                   tuple(nueva[c] for c in campos) + (sku,))

    if costing_write.activo():
        costing_write.guardar_finales(sku, dict(nueva), _mysql,
                                      accion=ACCION, origen=ORIGEN)
    else:
        # Corte apagado: mundo viejo, MySQL manda. No se espeja a kubera desde
        # aquí — el espejo lo hace el backend, no un script de mantenimiento.
        _mysql()

    # Bitácora. _log_costo es privado a propósito (resuelve la rama del corte F6
    # por su cuenta); reimplementarlo aquí sería duplicar esa decisión.
    costos._log_costo(sku, ACCION, ORIGEN, {
        "pct_comision": nueva["pct_comision"],
        "costo_comision": nueva["costo_comision"],
        "fuente_pct": origen_pct,
        "con_precio": con_precio,
    })


# ── Guardas ───────────────────────────────────────────────────────────────────

def ref_destino() -> str:
    """Los primeros caracteres del proyecto Supabase al que vamos a escribir."""
    url = settings.supabase_db_url or ""
    m = re.search(r"postgres\.([a-z0-9]+):", url) or re.search(r"db\.([a-z0-9]{20})\.", url)
    return m.group(1) if m else ""


def filas_objetivo(sku: str | None, limite: int) -> list[dict]:
    """
    Candidatas de costing.costos_finales. Se lee de kubera porque bajo el corte
    F6 es la primaria; sin corte, este script no debería usarse para escribir.
    """
    cols = ", ".join(("sku",) + COLS_KUBERA)
    sql = f"select {cols} from costing.costos_finales where canal = 'mercado_libre'"
    args: list[Any] = []
    if sku:
        sql += " and sku = %s"
        args.append(sku)
    sql += " order by sku"
    if limite:
        sql += " limit %s"
        args.append(limite)
    with sdb.get_cursor() as cur:
        cur.execute(sql, tuple(args))
        return [dict(r) for r in cur.fetchall()]


# ── Programa ──────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Actualiza SOLO la comisión de ML en costos_finales.")
    ap.add_argument("--real", action="store_true",
                    help="escribe de verdad (por defecto: dry-run, no toca nada)")
    ap.add_argument("--acepto-destino", default="",
                    help="primeros caracteres de la ref de Supabase; obligatorio con --real")
    ap.add_argument("--sku", default="", help="procesar un solo SKU")
    ap.add_argument("--limite", type=int, default=0, help="procesar solo los primeros N SKUs")
    ap.add_argument("--fuente", choices=("api", "ventas", "ambas"), default="ambas",
                    help="de dónde sacar el porcentaje (default: ambas, API primero)")
    ap.add_argument("--min-ventas", type=int, default=15,
                    help="líneas mínimas por categoría para fiarse del promedio medido")
    ap.add_argument("--sd-max", type=float, default=0.03,
                    help="desviación máxima aceptada en el promedio medido (default 3 puntos)")
    ap.add_argument("--min-delta", type=float, default=0.0,
                    help="cambio mínimo en el porcentaje para molestarse en escribir")
    ap.add_argument("--con-precio", action="store_true",
                    help="además recalcula precio_sugerido/precio_base (NO toca CBM ni Woo)")
    ap.add_argument("--cuenta", default=costos.DEFAULT_ACCOUNT, help="cuenta ML para la API")
    args = ap.parse_args()

    ref = ref_destino()
    if not ref:
        sys.exit("ABORT: no pude leer la ref de SUPABASE_DB_URL.")
    if args.real and not costing_write.activo():
        # Las candidatas se leen de costing.costos_finales (kubera). Eso solo es
        # correcto bajo el corte F6, donde kubera es la primaria. Con el corte
        # apagado la fuente de verdad es MySQL, y escribir lo leído de kubera
        # podría propagar un espejo desactualizado. El dry-run sí se permite.
        sys.exit("ABORT: el corte F6 de costing esta APAGADO (SUPABASE_WRITE_COSTING). "
                 "Este script lee de kubera como primaria; con el corte apagado "
                 "la fuente de verdad es MySQL. Aborto antes de escribir.")

    if args.real:
        # Nombrar el destino a mano es la única forma de que un --real disparado
        # con el .env equivocado se detenga antes de escribir.
        acepto = args.acepto_destino.strip()
        if len(acepto) < 6 or not ref.startswith(acepto):
            # Mensaje en ASCII puro: sys.exit escribe a stderr, que no pasa por
            # el reconfigure de arriba y en Windows sigue siendo cp1252.
            sys.exit("ABORT: --real exige --acepto-destino con al menos 6 caracteres "
                     f"que coincidan con la ref destino. Destino actual: {ref[:8]}")

    modo = "APLICANDO" if args.real else "DRY-RUN (no escribe nada)"
    print(f"destino kubera: {ref[:8]}…   corte F6 activo: {costing_write.activo()}")
    print(f"modo: {modo}   fuente: {args.fuente}   con-precio: {args.con_precio}\n")

    tasas = tasas_medidas(args.min_ventas) if args.fuente in ("ventas", "ambas") else {}
    if tasas:
        print(f"categorías con comisión medible (>= {args.min_ventas} ventas): {len(tasas)}\n")

    filas = filas_objetivo(args.sku or None, args.limite)
    print(f"SKUs a revisar: {len(filas)}\n")

    print(f"  {'sku':<20} {'categoría':<12} {'antes':>7} {'después':>8} {'fuente':<12} {'comisión $':>18}")
    print("  " + "-" * 84)

    cambios = saltados = iguales = sin_precio = 0
    for f in filas:
        viejo = float(f["pct_comision"]) if f["pct_comision"] is not None else None
        pct, origen_pct = resolver_pct(f, tasas, args.fuente, args.cuenta, args.sd_max)

        if pct is None:
            saltados += 1
            continue
        if not float(f.get("precio_sugerido") or 0):
            # Sin precio no hay comisión en pesos que calcular, y escribir NULL
            # en costo_comision sería borrar un dato: más de lo que este script
            # promete tocar. El SKU se deja como está.
            sin_precio += 1
            continue
        if viejo is not None and abs(pct - viejo) < max(args.min_delta, 1e-9):
            iguales += 1
            continue

        nueva = construir(f, pct, args.con_precio)
        antes_txt = f"{viejo * 100:.2f}%" if viejo is not None else "—"
        comision_txt = f"{f.get('costo_comision')} → {nueva['costo_comision']}"
        linea = (f"  {f['sku']:<20} {str(f.get('ml_cat_id') or '—'):<12}"
                 f" {antes_txt:>7} {pct * 100:>7.2f}% {origen_pct:<12} {comision_txt:>18}")
        if args.con_precio:
            # Con --con-precio el precio se mueve: verlo es el punto de la bandera.
            linea += f"   precio {f.get('precio_sugerido')} → {nueva['precio_sugerido']}"
        print(linea)

        if args.real:
            escribir(f["sku"], nueva, args.con_precio, origen_pct)
        cambios += 1

    print("\n  " + "-" * 84)
    print(f"  cambiados: {cambios}   sin cambio: {iguales}"
          f"   saltados (sin fuente fiable): {saltados}   saltados (sin precio): {sin_precio}")
    if not args.real:
        print("\n  DRY-RUN: no se escribió nada. Repetir con --real --acepto-destino "
              f"{ref[:8]} para aplicar.")


if __name__ == "__main__":
    main()
