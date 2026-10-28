"""
catalogo_precios_preparar.py — Saca del CATÁLOGO MAESTRO (CATALOGO_VIVO) los precios de mercado que muestra «Sugerir
precio» del Estudio (v0.696.0): `backend/data/catalogo_precios/precios.json.gz`.

    python backend/scripts/catalogo_precios_preparar.py CATALOGO_VIVO [SALIDA]

  CATALOGO_VIVO  la carpeta del catálogo (`reportes/catalogo_vivo`): lee
                 `datos/precios_v3/precios_catalogo_v3.json` (Mercado Libre) y `datos/mercado_amazon.json` (Amazon).
  SALIDA         por omisión `backend/data/catalogo_precios`.

QUÉ LLEVA Y QUÉ NO
    El repo es PÚBLICO: aquí solo viajan precios de MERCADO. Del v3 se toman `precio_ml_final` (la MEDIA), `min`,
    `max`, sus `fuentes` y la categoría de la banda; NO `estimado_costo`, `topado_10x_costo` ni `notas` (dicen «3.59×
    costo»: con el precio se despeja nuestro costo). De Amazon, la palabra clave, la estadística de los comparables y
    las publicaciones que se leyeron (familia, título, precio de la caja de compra).

    ⚠ `datos_pl.json` y `eduardo_ml/` traen costos y precios de compra: este script no los abre.

EL VEREDICTO DE CADA PUBLICACIÓN DE AMAZON
    El catálogo guardó, por grupo, cuántas publicaciones cayeron en cada clase (`clases`: m = mismo producto, g =
    otra gama, r = refacción, o = otro producto, d = dudoso), las 4 más baratas que sí eran el mismo (`ej`) y la
    estadística de las que entraron (n, media, mediana, p25, p75, mín, máx). No guardó el veredicto de cada una.
    Se reconstruye así (`veredictos`):
      · las de `ej` son match;
      · si el grupo usó solo las del mismo tamaño de paquete, se buscan TODOS los subconjuntos de tamaño n que dan
        exactamente esa estadística; lo que todos los que cuadran tienen en común es match o no match seguro;
      · lo que queda: si ya se contaron todas las de la clase «m», es no match; si no, `?` (sin veredicto guardado).

SOLO LEE Y ESCRIBE ARCHIVOS. No toca kubera, Woo, ML, Amazon ni Odoo.
"""
from __future__ import annotations

import gzip
import itertools
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
TITULO_MAX = 120
MAX_COMBINACIONES = 20_000          # 10 publicaciones → a lo más C(10,5) = 252; el tope es solo un seguro
CAMPOS_ESTADISTICA = ("min", "max", "media", "mediana", "p25", "p75")


def estadistica(precios: list[float]) -> dict[str, float]:
    """La MISMA cuenta de `CATALOGO_VIVO/scripts/mercado_comun.estadistica` (cuantil lineal, redondeo a 2)."""
    p = sorted(x for x in precios if x and x > 0)
    if not p:
        return {}

    def cuantil(f: float) -> float:
        if len(p) == 1:
            return p[0]
        i = f * (len(p) - 1)
        a, b = int(i), min(int(i) + 1, len(p) - 1)
        return p[a] + (p[b] - p[a]) * (i - a)

    return {"n": len(p), "media": round(statistics.fmean(p), 2), "mediana": round(statistics.median(p), 2),
            "p25": round(cuantil(0.25), 2), "p75": round(cuantil(0.75), 2),
            "min": round(p[0], 2), "max": round(p[-1], 2)}


def _cuadra(est: dict[str, float], grupo: dict) -> bool:
    return all(abs(est.get(k, -1) - float(grupo[k])) <= 0.011 for k in CAMPOS_ESTADISTICA if grupo.get(k) is not None)


def veredictos(grupo: dict, resultados: list[dict]) -> tuple[str, dict[int, int]]:
    """PURA. Una letra por publicación de `resultados` (m = match, x = no match, ? = sin veredicto guardado) y las
    unidades de las que hicieron match con otro tamaño de paquete ({índice: unidades})."""
    m = int((grupo.get("clases") or {}).get("m") or 0)
    ver = ["?"] * len(resultados)
    unidades: dict[int, int] = {}
    if m == 0:
        return "x" * len(resultados), unidades
    pos = {r.get("id"): i for i, r in enumerate(resultados)}
    exactos_ej, aprox_ej = set(), set()
    for e in grupo.get("ej") or []:
        i = pos.get(e.get("id"))
        if i is None:
            continue
        ver[i] = "m"
        if e.get("u"):
            aprox_ej.add(i)
            unidades[i] = int(e["u"])
        else:
            exactos_ej.add(i)
    n = int(grupo.get("n") or 0)
    seguros_m: set[int] = set(exactos_ej | aprox_ej)
    if n and not grupo.get("aprox"):
        # La estadística es de las n del MISMO tamaño: todas las combinaciones de n que la reproducen.
        libres = [i for i in range(len(resultados)) if i not in exactos_ej and i not in aprox_ej]
        faltan = n - len(exactos_ej)
        cuadran: list[set[int]] = []
        if 0 <= faltan <= len(libres):
            for k, combo in enumerate(itertools.combinations(libres, faltan)):
                if k >= MAX_COMBINACIONES:
                    cuadran = []
                    break
                elegidos = exactos_ej | set(combo)
                if _cuadra(estadistica([float(resultados[i].get("p") or 0) for i in elegidos]), grupo):
                    cuadran.append(elegidos)
        if cuadran:
            comunes = set.intersection(*cuadran)
            posibles = set.union(*cuadran)
            for i in comunes:
                ver[i] = "m"
            seguros_m |= comunes
            # Lo que ninguna combinación usa no es del mismo tamaño; si además ya están contadas todas las «m»
            # (las del mismo tamaño + las de otro paquete que salen en `ej`), tampoco es match.
            if m - n - len(aprox_ej) == 0:
                for i in range(len(resultados)):
                    if ver[i] == "?" and i not in posibles:
                        ver[i] = "x"
    if len(seguros_m) == m:
        ver = [v if v == "m" else "x" for v in ver]
    return "".join(ver), unidades


def _ml(fila: dict) -> dict | None:
    media = fila.get("precio_ml_final")
    if not media:
        return None
    out = {"media": round(float(media), 2), "min": fila.get("min"), "max": fila.get("max"),
           "fuentes": {k: round(float(v), 2) for k, v in (fila.get("fuentes") or {}).items() if v},
           "fuente": fila.get("fuente")}
    if fila.get("categoria_banda_v3"):
        out["categoria"] = fila["categoria_banda_v3"]
    if fila.get("revisar"):
        out["revisar"] = True
    return out


def armar(catalogo: Path) -> dict:
    v3 = json.loads((catalogo / "datos" / "precios_v3" / "precios_catalogo_v3.json").read_text(encoding="utf-8"))
    ma = json.loads((catalogo / "datos" / "mercado_amazon.json").read_text(encoding="utf-8"))
    skus: dict[str, dict] = {}
    for sku, fila in v3["filas"].items():
        if sku.startswith("PL:"):
            continue
        ml = _ml(fila)
        if ml:
            skus.setdefault(sku, {})["ml"] = ml
    busquedas: dict[str, list] = {}
    cuenta = {"m": 0, "x": 0, "?": 0}
    for sku, clave in ma["sku"].items():
        g = ma["grupos"].get(clave)
        if sku.startswith("PL:") or not g or not g.get("q"):
            continue
        resultados = ma["busquedas"].get(g["q"]) or []
        if g["q"] not in busquedas:
            busquedas[g["q"]] = [[r.get("id"), str(r.get("t") or "")[:TITULO_MAX], r.get("p")] for r in resultados]
        ver, unidades = veredictos(g, resultados)
        for v in ver:
            cuenta[v] += 1
        az = {"q": g["q"], "vistos": g.get("vistos", len(resultados)), "clases": g.get("clases") or {}, "v": ver}
        if unidades:
            az["u"] = {str(i): u for i, u in unidades.items()}
        if g.get("n"):
            az.update({k: g[k] for k in ("n", "media", "mediana", "p25", "p75", "min", "max") if k in g})
            if g.get("aprox"):
                az["aprox"] = 1
        skus.setdefault(sku, {})["az"] = az
    return {
        "meta": {
            "fuente": "Catálogo maestro (CATALOGO_VIVO)",
            "ml_fecha": (v3.get("meta") or {}).get("fecha"),
            "ml_version": (v3.get("meta") or {}).get("version"),
            "razon_amazon_ml": (v3.get("meta") or {}).get("razon_amazon_ml"),
            "amazon_actualizado": ma.get("actualizado"),
            "generado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "skus": len(skus),
            "con_ml": sum(1 for x in skus.values() if "ml" in x),
            "con_amazon": sum(1 for x in skus.values() if (x.get("az") or {}).get("n")),
            "veredictos": cuenta,
        },
        "skus": skus,
        "busquedas": busquedas,
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    catalogo = Path(sys.argv[1])
    salida = Path(sys.argv[2]) if len(sys.argv) > 2 else _BACKEND / "data" / "catalogo_precios"
    datos = armar(catalogo)
    salida.mkdir(parents=True, exist_ok=True)
    crudo = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    (salida / "precios.json.gz").write_bytes(gzip.compress(crudo, 9, mtime=0))
    m = datos["meta"]
    print(f"{m['skus']} SKUs · {m['con_ml']} con precio de ML · {m['con_amazon']} con precio de Amazon · "
          f"{len(datos['busquedas'])} búsquedas · veredictos {m['veredictos']} · "
          f"{len(crudo) / 1e6:.1f} MB → {(salida / 'precios.json.gz').stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
