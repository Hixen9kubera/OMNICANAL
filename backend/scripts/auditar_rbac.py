"""
auditar_rbac.py — ¿qué endpoints piden admin porque alguien lo DECIDIÓ, y
cuáles porque nadie los clasificó?

POR QUÉ EXISTE

`core/rbac.py` hace que lo no listado nazca cerrado, y eso está bien: el peor
caso de un olvido es un 403, no una puerta abierta. El problema es CÓMO nos
enteramos del olvido. Hasta hoy, así:

    26-ago  un KAM abre "Métricas"          → 403 → Slack     (/api/analisis)
    01-sep  Andrea valida costos publicados → 403 → Slack     (/api/costos-publicados)
    01-sep  el mismo día, sin detonar: PUT del Estudio, DELETE de la marca
            de COSTO VALIDADO — los dos con su hermano POST ya listado.

Tres veces el mismo defecto, descubierto tres veces por alguien que no podía
trabajar. Este script contesta la pregunta ANTES, sin levantar la app (no
necesita base de datos): parsea los decoradores de `routers/*.py` y los pasa por
la misma tabla que usa el middleware.

    python backend/scripts/auditar_rbac.py            # solo lo no clasificado
    python backend/scripts/auditar_rbac.py --todo     # las 170 rutas por rol

Salir con código 1 cuando hay rutas sin clasificar lo deja listo para un hook o
un CI. NO se enchufa a ninguno todavía: primero hay que clasificar las que ya
están, o nacería en rojo y se aprendería a ignorarlo.

LO QUE ESTE SCRIPT **NO** VE, y hay que saberlo para no confiarse:

  · rutas que no se declaran con `@router.<verbo>("...")` literal (montajes,
    include_router anidados, decoradores calculados);
  · si el rol asignado es el CORRECTO — solo si alguien lo asignó. Que
    `/api/fanout` pida admin es una decisión; que la pidiera `/api/costos-
    publicados` era un descuido, y desde fuera se ven idénticos.

Por eso la salida separa EXPLÍCITO de POR OMISIÓN: la segunda lista es la única
que hay que mirar.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from core import rbac  # noqa: E402

RUTAS = pathlib.Path(__file__).resolve().parents[1] / "routers"

_DECORADOR = re.compile(r'@router\.(get|post|patch|put|delete)\(\s*[fr]?["\']([^"\']*)["\']')
_PREFIJO = re.compile(r'APIRouter\(\s*prefix=["\']([^"\']+)["\']')


def _explicita(metodo: str, ruta: str) -> bool:
    """¿Hay una regla que nombre a este (método, prefijo), o cae al default?"""
    return any(metodo == m and ruta.startswith(p) for m, p, _ in rbac.REGLAS)


def censo() -> list[tuple[str, str, str, bool, str]]:
    """(rol, método, ruta, explícita, archivo) de cada endpoint declarado."""
    filas = []
    for archivo in sorted(RUTAS.glob("*.py")):
        texto = archivo.read_text(encoding="utf-8", errors="replace")
        m = _PREFIJO.search(texto)
        prefijo = m.group(1) if m else ""
        for verbo, sufijo in _DECORADOR.findall(texto):
            ruta = (prefijo + sufijo) or "/"
            if not ruta.startswith("/api"):
                continue
            metodo = verbo.upper()
            filas.append((rbac.rol_requerido(metodo, ruta), metodo, ruta,
                          _explicita(metodo, ruta), archivo.name))
    return filas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--todo", action="store_true",
                    help="imprime también las rutas ya clasificadas, por rol")
    args = ap.parse_args()

    filas = censo()
    huerfanas = [f for f in filas if not f[3]]

    print(f"{len(filas)} rutas declaradas · {len(filas) - len(huerfanas)} clasificadas "
          f"· {len(huerfanas)} por omision\n")

    if args.todo:
        for rol in ("admin", "operador", "lectura"):
            grupo = [f for f in filas if f[0] == rol and f[3]]
            print(f"== {rol.upper()} explicito ({len(grupo)}) ==")
            for _, metodo, ruta, _, arch in sorted(grupo, key=lambda x: (x[4], x[2])):
                print(f"  {metodo:6} {ruta:56} {arch}")
            print()

    if not huerfanas:
        print("Todas las rutas tienen una regla que las nombra.")
        return 0

    print(f"== SIN CLASIFICAR -> caen a {rbac.ROL_POR_DEFECTO} ({len(huerfanas)}) ==")
    print("   Puede estar bien. Lo que no puede es que nadie lo haya decidido.\n")
    for _, metodo, ruta, _, arch in sorted(huerfanas, key=lambda x: (x[4], x[2])):
        print(f"  {metodo:6} {ruta:56} {arch}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
