"""
catalogo_vivo.py — El catálogo de Odoo cruzado EN VIVO contra los marketplaces.

    python catalogo_vivo.py odoo            # catálogo, free_qty, contenedor y fotos
    python catalogo_vivo.py woo categorias  # precio de catálogo y categoría de mercado
    python catalogo_vivo.py amazon walmart  # uno o varios canales (evidencia de precio)
    python catalogo_vivo.py todo            # todo lo que tenga credenciales
    python catalogo_vivo.py pagina          # arma index.html con lo ya extraído

    --salida CARPETA   dónde dejar todo (por omisión `../salidas/`, fuera de git)

Cada etapa escribe su propio archivo en `datos/` y es independiente: si un canal
falla, los demás quedan como estaban y la página dice cuál NO se leyó. Un canal
que no se pudo leer NUNCA se rellena con un caché: sale como «no verificado».

No publica nada. No escribe nada. En ningún lado. Ver `comun.py`.
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path

from comun import SALIDAS, Cfg, ahora_iso, aviso, consola_utf8, escribir_json

ETAPAS = ("odoo", "costos", "woo", "amazon", "walmart", "ml", "tiktok", "temu", "categorias",
          "imagenes", "pagina")


def _correr(nombre: str, cfg: Cfg, salida: Path, args: argparse.Namespace) -> dict:
    if nombre == "odoo":
        import f_odoo
        return f_odoo.extraer(cfg, salida, con_fotos=not args.sin_fotos)
    if nombre == "costos":
        import f_costos
        return f_costos.extraer(cfg, salida)
    if nombre == "woo":
        import f_woo
        return f_woo.extraer(cfg, salida)
    if nombre == "amazon":
        import f_amazon
        return f_amazon.extraer(cfg, salida)
    if nombre == "walmart":
        import f_walmart
        return f_walmart.extraer(cfg, salida)
    if nombre == "ml":
        import f_ml
        return f_ml.extraer(cfg, salida, con_economia=not args.sin_economia)
    if nombre == "tiktok":
        import f_tiktok
        return f_tiktok.extraer(cfg, salida)
    if nombre == "temu":
        import f_temu
        return f_temu.extraer(cfg, salida)
    if nombre == "categorias":
        import f_categorias
        return f_categorias.extraer(cfg, salida)
    if nombre == "imagenes":
        import imagenes
        return imagenes.construir(salida)
    if nombre == "pagina":
        import pagina
        return pagina.construir(salida)
    raise ValueError(nombre)


def main() -> int:
    consola_utf8()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("etapas", nargs="+", choices=ETAPAS + ("todo",))
    ap.add_argument("--salida", default=str(SALIDAS))
    ap.add_argument("--sin-fotos", action="store_true", help="Odoo sin image_128")
    ap.add_argument("--sin-economia", action="store_true",
                    help="ML sin precio cobrado / comisión / envío (3 GET por publicación activa)")
    args = ap.parse_args()
    salida = Path(args.salida).resolve()
    salida.mkdir(parents=True, exist_ok=True)
    etapas = list(ETAPAS) if "todo" in args.etapas else [e for e in ETAPAS if e in args.etapas]

    cfg = Cfg()
    fallas = 0
    for nombre in etapas:
        t0 = time.monotonic()
        aviso(f"── {nombre}")
        # Un archivo de estado POR etapa: así dos etapas pueden correr a la vez en
        # procesos distintos sin pisarse la bitácora.
        try:
            resumen = _correr(nombre, cfg, salida, args)
            estado = {"ok": True, "cuando": ahora_iso(),
                      "segundos": round(time.monotonic() - t0, 1), "resumen": resumen}
        except Exception as exc:  # noqa: BLE001 — una etapa rota no tumba a las demás
            fallas += 1
            aviso(f"   {nombre} FALLÓ: {type(exc).__name__}: {exc}")
            traceback.print_exc()
            estado = {"ok": False, "cuando": ahora_iso(),
                      "segundos": round(time.monotonic() - t0, 1),
                      "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
        escribir_json(salida / "datos" / "_estado" / f"{nombre}.json", estado)
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
