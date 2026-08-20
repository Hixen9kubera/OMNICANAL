"""
triar_excepts_mysql.py — Los `try/except` que se tragan un fallo de MySQL, y
cuáles de ellos DECIDEN algo.

POR QUÉ
-------
Medido el 20-ago: **95 bloques** llaman al MySQL de `kubera_ml` dentro de un
`try` cuyo `except` no relanza. Cero relanzan. O sea que el día que se apague
MySQL —el corte— el sistema **no se cae: contesta**. Contesta "no hay", "no está
publicado", "no lo he hecho", en 95 lugares y sin un error en pantalla.

Es el mecanismo de los 964 pedidos fantasma del 12-ago, multiplicado.

Pero NO hay que arreglar los 95. La pregunta que separa los caros de los
molestos es la misma de CLAUDE.md: **¿alguien LEE esto PARA DECIDIR?**

  · si el resultado pinta una pantalla  → molesto, se ve, se arregla después
  · si el resultado decide una ESCRITURA → caro, y hay que arreglarlo ANTES

CÓMO CLASIFICA
--------------
Automático hasta donde se puede, y marcando lo dudoso en vez de adivinar:

  ESCRIBE   el propio bloque hace INSERT/UPDATE/DELETE — perder eso es perder
            un dato, pero no decide mal; se reprocesa
  DECIDE    el valor del `try` alimenta un `if`, un `not`, o se devuelve desde
            una función cuyo nombre pregunta algo (`_ya_`, `existe`, `previo`,
            `disponible`, `hay_`) — este es el grupo caro
  MUESTRA   el resto: alimenta una lista, un conteo, una respuesta de API

La clasificación automática es una PRIMERA PASADA, no un veredicto. Cada
DECIDE hay que abrirlo. El script lo dice en su propia salida para que nadie
confunda una heurística con una medición.

Uso:
  ...python backend/scripts/triar_excepts_mysql.py
  ...python backend/scripts/triar_excepts_mysql.py --detalle
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections import Counter
from pathlib import Path

BACK = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_DB = ("fetch_all", "fetch_one", "fetch_scalar", "execute", "get_cursor", "executemany")
_ESCRIBE = ("insert into", "update ", "delete from", "replace into")
# Nombres que delatan una PREGUNTA: su respuesta se usa para decidir.
_PREGUNTAS = ("_ya_", "ya_", "existe", "previo", "disponible", "hay_", "esta_",
              "tiene_", "_es_", "puede_", "conocid", "vist")


def llama_db(nodo) -> bool:
    for n in ast.walk(nodo):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "db"
                and n.func.attr in _DB):
            return True
    return False


def sql_del_bloque(nodo) -> str:
    txt = []
    for n in ast.walk(nodo):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            txt.append(n.value)
        elif isinstance(n, ast.JoinedStr):
            txt += [v.value for v in n.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    return " ".join(txt).lower()


def clasificar(bloque, fn_nombre: str, fuente: list[str]) -> tuple[str, str]:
    sql = sql_del_bloque(bloque)
    if any(k in sql for k in _ESCRIBE):
        return "ESCRIBE", "el bloque escribe; perderlo pierde un dato, no decide mal"

    nom = (fn_nombre or "").lower()
    if any(p in nom for p in _PREGUNTAS):
        return "DECIDE", f"la funcion `{fn_nombre}` es una PREGUNTA por su nombre"

    # ¿El valor que sale del try alimenta un `if` / `not` / `while`?
    asignados = set()
    for n in ast.walk(bloque):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    asignados.add(t.id)
    if asignados:
        cuerpo = "\n".join(fuente[bloque.end_lineno: bloque.end_lineno + 25])
        for v in asignados:
            for patron in (f"if {v}", f"if not {v}", f"while {v}",
                           f"if {v} is", f"and {v}", f"or {v}"):
                if patron in cuerpo:
                    return "DECIDE", f"`{v}` alimenta un `{patron.split()[0]}` justo despues"
    return "MUESTRA", "alimenta una lista, un conteo o una respuesta"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detalle", action="store_true")
    args = ap.parse_args()

    hallazgos = []
    for f in sorted(list((BACK / "services").glob("*.py"))
                    + list((BACK / "routers").glob("*.py"))):
        texto = f.read_text(encoding="utf-8", errors="replace")
        fuente = texto.splitlines()
        try:
            arbol = ast.parse(texto)
        except SyntaxError:
            continue
        # De cada `try`, la funcion que lo contiene.
        padres = {}
        for fn in ast.walk(arbol):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for n in ast.walk(fn):
                    if isinstance(n, ast.Try):
                        padres.setdefault(id(n), fn.name)
        for n in ast.walk(arbol):
            if not isinstance(n, ast.Try) or not n.handlers or not llama_db(n):
                continue
            if any(any(isinstance(x, ast.Raise) for x in ast.walk(h))
                   for h in n.handlers):
                continue                      # este si relanza: no aplica
            fn = padres.get(id(n), "(nivel de modulo)")
            tipo, motivo = clasificar(n, fn, fuente)
            hallazgos.append({"archivo": str(f.relative_to(BACK)), "linea": n.lineno,
                              "fn": fn, "tipo": tipo, "motivo": motivo})

    print("TRIAJE DE LOS `except` QUE SE TRAGAN UN FALLO DE MySQL\n")
    c = Counter(h["tipo"] for h in hallazgos)
    print(f"  total: {len(hallazgos)}")
    for t in ("DECIDE", "ESCRIBE", "MUESTRA"):
        print(f"    {t:9s} {c.get(t, 0):3d}")

    print(f"\n{'═' * 74}\n  DECIDE — el grupo caro. Hay que abrir cada uno.\n{'═' * 74}")
    for h in [x for x in hallazgos if x["tipo"] == "DECIDE"]:
        print(f"  {h['archivo']}:{h['linea']}")
        print(f"      {h['fn']}()  —  {h['motivo']}")

    if args.detalle:
        for t in ("ESCRIBE", "MUESTRA"):
            print(f"\n{'─' * 74}\n  {t}\n{'─' * 74}")
            for h in [x for x in hallazgos if x["tipo"] == t]:
                print(f"  {h['archivo']:42s}:{h['linea']:<5d} {h['fn']}()")
    else:
        print(f"\n  (ESCRIBE y MUESTRA se listan con --detalle)")

    print(f"\n  ⚠ Esto es una PRIMERA PASADA por heuristica, no un veredicto.")
    print(f"    Cada DECIDE hay que leerlo: el nombre de una funcion miente")
    print(f"    mas facil que una medicion.")


if __name__ == "__main__":
    main()
