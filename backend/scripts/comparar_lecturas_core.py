"""
comparar_lecturas_core.py — Arnés de paridad de las lecturas F5 del dominio
CORE: los lookups SKU→wc_id gemelos (MySQL `productos` vs kubera
`core.products` vía services/core_read.py).

Reglas del dominio:
  - Muestra = TODOS los SKUs vendidos en los últimos 30 días (la ruta caliente
    real de resolver_producto) + 300 al azar del maestro MySQL.
  - ESTRICTO donde MySQL tiene wc_id: kubera debe devolver el mismo
    (wc_id, wc_parent_id). Si kubera devuelve None se clasifica aparte
    (hueco del seam Crear — el llamador reconsulta MySQL, no es error F5,
    pero se reporta el conteo).
  - solo_en_kubera NO es delta: core.products absorbe Woo vía ETL y es MÁS
    completo que `productos` (congelado 23-jul).
  - ARBITRAJE: cuando MySQL y kubera difieren, WordPress VIVO decide. Hallazgo
    de la primera corrida (03-ago): 27 SKUs que se volvieron variación después
    del congelamiento — MySQL conserva el id viejo, kubera trae la variación
    real (verificado contra wp_posts). kubera==Woo NO es delta; es el espejo
    corrigiendo a la fuente.
  - buscar_wc_ids (gemela F6, sin cablear) se prueba INFORMATIVO: total
    kubera >= total MySQL con los mismos filtros.

SOLO LECTURA. Uso:  python backend/scripts/comparar_lecturas_core.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import core_read, db  # noqa: E402


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    vendidos = [r["sku"] for r in db.fetch_all(
        """SELECT DISTINCT s.sku FROM (
             SELECT TRIM(SUBSTRING_INDEX(SUBSTRING_INDEX(skus, ',', n.n), ',', -1)) sku
             FROM pedidos_ml
             JOIN (SELECT 1 n UNION SELECT 2 UNION SELECT 3 UNION SELECT 4
                   UNION SELECT 5) n
               ON n.n <= 1 + LENGTH(skus) - LENGTH(REPLACE(skus, ',', ''))
             WHERE creado >= NOW() - INTERVAL 30 DAY AND skus IS NOT NULL
           ) s WHERE s.sku <> ''""")]
    azar = [r["sku"] for r in db.fetch_all(
        "SELECT sku FROM productos WHERE sku IS NOT NULL AND sku <> '' "
        "ORDER BY RAND(42) LIMIT 300")]
    muestra = sorted(set(vendidos) | set(azar))
    print(f"Muestra: {len(vendidos)} SKUs vendidos (30d) + {len(azar)} al azar "
          f"= {len(muestra)} únicos")

    ph = ",".join(["%s"] * len(muestra))
    my = {r["sku"]: r for r in db.fetch_all(
        f"SELECT sku, wc_id, wc_parent_id FROM productos WHERE sku IN ({ph})",
        tuple(muestra))}

    iguales = difs = ausentes_kubera = sin_wc_mysql = fuera_mysql = 0
    detalle = []
    for sku in muestra:
        rm = my.get(sku)
        rk = core_read.wc_de_sku(sku)
        if rm is None or not rm.get("wc_id"):
            # MySQL no lo resuelve: la ruta real sigue a Woo API en ambos mundos
            fuera_mysql += 1 if rm is None else 0
            sin_wc_mysql += 1 if rm is not None else 0
            continue
        if rk is None:
            ausentes_kubera += 1  # hueco del seam: el llamador reconsulta MySQL
            continue
        par_my = (int(rm["wc_id"]), int(rm["wc_parent_id"]) if rm.get("wc_parent_id") else None)
        par_kb = (int(rk["wc_id"]), int(rk["wc_parent_id"]) if rk.get("wc_parent_id") else None)
        if par_my == par_kb:
            iguales += 1
        else:
            difs += 1
            detalle.append({"sku": sku, "mysql": par_my, "kubera": par_kb})

    # arbitraje de las diferencias contra WordPress VIVO (la verdad)
    kubera_gana = mysql_gana = empate_raro = 0
    if detalle:
        from services.wp_db import _fetch_all as _wp
        for d in detalle:
            rows = _wp(
                """SELECT p.ID, p.post_parent
                   FROM wp_posts p JOIN wp_postmeta pm
                     ON pm.post_id=p.ID AND pm.meta_key='_sku'
                   WHERE pm.meta_value=%s AND p.post_status NOT IN ('trash','auto-draft')""",
                (d["sku"],))
            woo = ((int(rows[0]["ID"]), int(rows[0]["post_parent"]) or None)
                   if rows else None)
            d["woo"] = woo
            if woo == tuple(d["kubera"]):
                kubera_gana += 1
            elif woo == tuple(d["mysql"]):
                mysql_gana += 1
            else:
                empate_raro += 1
    difs_reales = mysql_gana + empate_raro

    print(f"lookup wc_de_sku: iguales={iguales} difs={difs} "
          f"ausentes_en_kubera={ausentes_kubera} (seam) "
          f"sin_wc_en_mysql={sin_wc_mysql} fuera_de_mysql={fuera_mysql}")
    if detalle:
        print(f"arbitraje Woo vivo: kubera_correcto={kubera_gana} "
              f"mysql_correcto={mysql_gana} ninguno={empate_raro} "
              f"-> difs_reales={difs_reales}")
    for d in detalle[:10]:
        print("   ", json.dumps(d, ensure_ascii=False))

    # informativo: gemela F6 del listado (kubera debe ser superconjunto)
    t_my = int(db.fetch_scalar(
        "SELECT COUNT(*) FROM productos WHERE wc_id IS NOT NULL "
        "AND (status_wc IS NULL OR status_wc <> 'draft')") or 0)
    _ids, t_kb = core_read.buscar_wc_ids(None, None, [], "reciente", 1, 1)
    print(f"listado (informativo F6): total mysql={t_my} kubera={t_kb} "
          f"-> {'kubera ⊇ mysql OK' if t_kb >= t_my else 'KUBERA INCOMPLETO'}")

    ok = difs_reales == 0 and t_kb >= t_my
    print("VEREDICTO:", "EQUIVALENTE" if ok else "CON DIFERENCIAS")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
