"""
aplicar_migraciones.py — Crea/actualiza el SANDBOX con supabase/migrations/:
REGISTRA cada archivo en ops.migraciones, SALTA lo ya registrado y al final
valida la paridad del esquema contra supabase/schema_manifest.json (la foto de
la BD kubera de producción).

CANDADO DURO: se NIEGA a correr si el destino (SUPABASE_DB_URL de env.staging)
es el proyecto de producción — comparando contra SUPABASE_PROD_REF y contra la
ref conocida de la BD kubera. Este script existe para el sandbox, punto.

POR QUÉ REGISTRA (v0.628.0, fase 0 del reorden de esquemas)
------------------------------------------------------------
Hasta la v0.627.0 volvía a ejecutar TODOS los *.sql en cada corrida y no anotaba
nada. Eso solo funciona mientras cada archivo sea re-ejecutable sobre la base de
HOY; en cuanto una tabla se mude de esquema (`alter table … set schema`), la
0033, 0058, 0061, 0064 y 0065 recrearían en `ops` copias vacías, su `create or
replace` regresaría las funciones al texto viejo y un `set schema` repetido
truena con 42P07. Ahora el registro manda:

  · Cada archivo corre en UNA transacción junto con su fila en ops.migraciones
    (la crea la 0064; es de solo agregar). Si el archivo trae su propio
    `begin;` … `commit;` (una pareja, al nivel de arriba), el runner quita esas
    dos líneas y lo envuelve en la suya: o queda todo (DDL + fila), o nada.
  · Si el archivo ya inserta su propia fila (0064 a 0070, en cualquiera de sus
    formas: `values (…)`, `select '…'` dentro de un DO como la 0068, o con
    `where not exists` como la 0069 y la 0070), no se duplica: el runner cuenta
    las filas antes y después. ops.migraciones NO tiene un único sobre el
    nombre (ni en la 0064 ni en producción): quien no duplica es el runner. Si
    el archivo inserta una con OTRO nombre (un archivo renumerado que no cambió
    su insert) se rechaza ANTES de tocar la base (también en --plan), y si en la
    base aparece igual, se deshace entero: esa fila fantasma movería la
    frontera y no se podría borrar. Si deja más de una con su nombre en una
    sola corrida, también se deshace.
  · Un archivo que falla se deshace entero, sin fila, y la corrida se detiene:
    lo anterior queda confirmado y registrado; la próxima corrida empieza en él.
    (Salvo en una base nueva ANTES de la 0064: ahí lo confirmado no tiene fila;
    el runner lo dice y la próxima corrida se detiene — usa --recrear.)

QUÉ DECIDE, SEGÚN LA BASE
-------------------------
  · Base NUEVA (`--recrear`, o sin ops.migraciones Y sin tablas en los
    esquemas propios): corre todo en orden. Lo que corre antes de que exista
    ops.migraciones (0001…0063) se registra en la misma transacción del archivo
    que la crea (la 0064), con modo `registro_tardio`; de ahí en adelante, cada
    archivo con el suyo. `--recrear` revisa ANTES de tirar nada que existan los
    objetos que la cadena toca sin crear (hoy la 0025: propuestas_retirado.* y
    public.packing_*); si faltan, se detiene sin tirar nada. En el sandbox
    faltan desde que la 0052 (fuera de main) los quitó: ahí --recrear NO está
    soportado hasta que exista una migración de compatibilidad con su acta.
  · Sin ops.migraciones pero CON tablas (una corrida a medias antes de la 0064,
    una restauración vieja): se detiene. Re-correr desde la 0001 encima de lo
    vivo no es seguro (la 0033 vieja truena contra la forma de la 0036).
  · Base EXISTENTE (el sandbox): UNA vez, `--adoptar-hasta NNNN` registra como
    aplicadas, SIN ejecutarlas, las migraciones ≤ NNNN que no tengan fila. Solo
    por debajo de lo ya registrado (la «frontera»: el número más alto con fila).
    NO se supone que todo lo de abajo esté aplicado: el sandbox no lo armó solo
    este runner (hubo scripts de aplicación sueltos) y medido el 8-oct le
    faltaban la 0043, 0044_blindaje, 0045, 0046, 0050 y 0051. Por eso antes de
    adoptar compara la HUELLA de cada una contra el catálogo: tablas, vistas,
    columnas (agregadas o renombradas), RLS, security_invoker, funciones (por
    número de argumentos y cuerpo), índices, triggers, constraints, policies,
    esquemas, y lo que tira y debe ya no estar. Si falta algo —o si la
    migración no tiene NADA verificable (solo grants, comments, datos)— no
    adopta a ciegas: se nombra en `--excepto` (no se adopta; se corre después)
    o en `--sin-huella-ok` (se adopta igual: p. ej. lo tiró una migración que no
    está en main, o era un no-op en esa base). La que hizo algo verificable que
    una posterior reemplazó entero se adopta sin más (no cambia nada). La huella
    NO ve cuerpos de vistas, grants, comments ni datos.
  · Por omisión: salta lo registrado y corre y registra lo nuevo, en orden. Si
    hay pendientes POR DEBAJO de la frontera («atrasadas»: un .sql que llegó
    tarde a main, o lo que se excluyó al adoptar), se detiene y las nombra:
    `--incluir-atrasadas` las corre (en orden de archivo), o se adoptan.
    `--hasta NNNN` corre solo las pendientes <= NNNN; las de arriba siguen
    pendientes (el 9-oct el sandbox recibió la 0068 y la 0069 y no la 0070).
  · `--plan` imprime qué correría, saltaría o adoptaría, SIN escribir (lee en
    una transacción `read only` que termina en ROLLBACK; nunca marca la sesión).

Uso (desde la raíz del repo o de un worktree; lee SUPABASE_DB_URL y
SUPABASE_PROD_REF de env.staging —de la raíz, o ../OMNICANAL/env.staging—, y
nada más de ese archivo):
  backend/.venv/Scripts/python.exe backend/scripts/aplicar_migraciones.py --plan
  backend/.venv/Scripts/python.exe backend/scripts/aplicar_migraciones.py --adoptar-hasta 0063 --plan
  backend/.venv/Scripts/python.exe backend/scripts/aplicar_migraciones.py --adoptar-hasta 0063 [--excepto A,B] [--sin-huella-ok C]
  backend/.venv/Scripts/python.exe backend/scripts/aplicar_migraciones.py            # aplica lo nuevo + verifica
  backend/.venv/Scripts/python.exe backend/scripts/aplicar_migraciones.py --incluir-atrasadas
  backend/.venv/Scripts/python.exe backend/scripts/aplicar_migraciones.py --hasta 0069 [--plan]
  backend/.venv/Scripts/python.exe backend/scripts/aplicar_migraciones.py --verificar-solo
  backend/.venv/Scripts/python.exe backend/scripts/aplicar_migraciones.py --recrear  # base desechable: tira y corre todo (si están sus requisitos)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
MIGRACIONES = ROOT / "supabase" / "migrations"
MANIFIESTO = ROOT / "supabase" / "schema_manifest.json"
REF_KUBERA_PROD = "tukwcvsi"  # ref conocida de la BD kubera (producción operativa)

# El mismo patrón que el CHECK migraciones_nombre_chk de ops.migraciones (0064).
NOMBRE_RE = re.compile(r"^[0-9]{4}_[a-z0-9_]+$")
POR = "aplicar_migraciones.py"
# Candado de transacción (pg_advisory_xact_lock): dos corridas a la vez se
# forman, y la segunda ve la fila de la primera y salta. Muere con el commit;
# no deja nada pegado en el pooler.
CANDADO = 6602866  # cualquier entero fijo: es la llave del candado

ESQUEMAS_PROPIOS = ("core", "channel", "costing", "enrich", "ops", "migration",
                    "ventas", "almacen")


def _watchdog():
    def _matar():
        print("WATCHDOG: 10 min — aborto.", flush=True)
        os._exit(2)
    t = threading.Timer(600, _matar)
    t.daemon = True
    t.start()


# ═══════════════════════════════════════════════════════════════════════════
# Entorno: solo dos llaves de env.staging (el archivo trae las de MySQL/Woo)
# ═══════════════════════════════════════════════════════════════════════════
LLAVES_ENV = ("SUPABASE_DB_URL", "SUPABASE_PROD_REF")


def ruta_env(explicita: str | None) -> Path:
    if explicita:
        return Path(explicita)
    propia = ROOT / "env.staging"
    return propia if propia.exists() else ROOT.parent / "OMNICANAL" / "env.staging"


def cargar_env(ruta: Path) -> dict[str, str]:
    """os.environ más SOLO las llaves de LLAVES_ENV del archivo (que mandan)."""
    vals: dict[str, str] = {k: os.environ[k] for k in LLAVES_ENV if k in os.environ}
    if not ruta.exists():
        return vals
    with ruta.open(encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, _, v = s.partition("=")
            if k.strip() in LLAVES_ENV:
                vals[k.strip()] = v.split("#")[0].strip().strip('"').strip("'")
    return vals


def ref_sandbox(env: dict[str, str]) -> str:
    """La ref del destino, o aborta si no es válida o si es PRODUCCIÓN."""
    url = env.get("SUPABASE_DB_URL", "")
    m = re.search(r"postgres\.([a-z0-9]+):", url)
    if not m:
        sys.exit("ABORT: env.staging no tiene SUPABASE_DB_URL válida (¿ya pegaste las llaves del sandbox?).")
    ref = m.group(1)
    prod_ref = env.get("SUPABASE_PROD_REF", "").strip()
    if ref.startswith(REF_KUBERA_PROD) or (prod_ref and ref == prod_ref):
        sys.exit(f"ABORT: el destino ({ref[:8]}…) es la BD kubera de PRODUCCIÓN. "
                 "Este script solo aplica migraciones al SANDBOX.")
    return ref


# ═══════════════════════════════════════════════════════════════════════════
# Los archivos
# ═══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Migracion:
    nombre: str   # '0064_ops_ordenes_venta' (lo que va en ops.migraciones.migracion)
    numero: int   # 64
    ruta: Path


def listar_migraciones(carpeta: Path = MIGRACIONES) -> list[Migracion]:
    """En el MISMO orden de siempre (`sorted(glob)`): respeta las numeraciones
    repetidas (0004, 0018, 0023, 0025, 0033, 0043, 0044)."""
    out = []
    for f in sorted(carpeta.glob("*.sql")):
        if not NOMBRE_RE.match(f.stem):
            raise ValueError(f"{f.name}: el nombre no cumple {NOMBRE_RE.pattern} "
                             "(el CHECK de ops.migraciones lo rechazaría)")
        out.append(Migracion(f.stem, int(f.stem[:4]), f))
    return out


_CONTROL = re.compile(
    r"^[ \t]*(begin|commit|rollback|start[ \t]+transaction|abort)[ \t]*;[ \t]*(?:--[^\n]*)?\r?$",
    re.I | re.M)


def en_una_transaccion(texto: str, nombre: str = "") -> str:
    """El texto listo para correr DENTRO de la transacción del runner.

    Sin control de transacción, tal cual. Con UNA pareja `begin;` … `commit;` al
    nivel de arriba (una línea cada una; la forma de la casa), esas dos líneas se
    vuelven comentario —los números de línea no se mueven— y el archivo queda
    dentro de la transacción del runner, junto con su fila. Cualquier otra forma
    (dos commits, un rollback) truena ANTES de ejecutar nada: con un commit a la
    mitad, la fila ya no podría ir en la misma transacción.

    `end;` NO se toma como control: es el cierre de bloque de plpgsql y va suelto
    en su línea dentro de los cuerpos `$$ … $$`. Un `begin` de plpgsql nunca
    lleva `;`, así que no se confunde.
    """
    marcas = list(_CONTROL.finditer(texto))
    if not marcas:
        return texto
    tipos = [re.sub(r"\s+", " ", m.group(1).lower()) for m in marcas]
    if len(marcas) == 2 and tipos[0] in ("begin", "start transaction") and tipos[1] == "commit":
        partes, previo = [], 0
        for m in marcas:
            partes.append(texto[previo:m.start()])
            partes.append(f"-- [{POR}: «{m.group(0).strip()}» lo pone el runner]")
            previo = m.end()
        partes.append(texto[previo:])
        return "".join(partes)
    raise ValueError(f"{nombre or 'archivo'}: control de transacción no soportado "
                     f"({', '.join(tipos)}); el runner envuelve cada archivo en UNA transacción")


def huella_sha(texto: str) -> str:
    """sha256 del texto con saltos LF (igual en un checkout CRLF de Windows)."""
    return hashlib.sha256(texto.replace("\r\n", "\n").encode("utf-8")).hexdigest()


_INSERT_REGISTRO = re.compile(r"\binsert\s+into\s+ops\.migraciones\b(.*?);", re.I | re.S)
_LITERAL_MIGRACION = re.compile(r"'([0-9]{4}_[a-z0-9_]+)'")


def registros_propios(texto: str) -> list[str]:
    """Los nombres que el ARCHIVO mismo inserta en ops.migraciones (las que se
    registran solas: 0064-0070). Salen de los literales 'NNNN_…' de cada
    `insert into ops.migraciones … ;`, en cualquiera de las formas de la casa:
    `values ('…', …)` (0064-0067), `select '…', …` dentro de un DO (0068) o
    `select … where not exists (…)` (0069, 0070). Sin comentarios. Lista vacía:
    la fila la pone el runner."""
    sql = _modelo_rls()._sin_comentarios(texto)
    return sorted({n for x in _INSERT_REGISTRO.finditer(sql) for n in _LITERAL_MIGRACION.findall(x.group(1))})


def revisar_registro_propio(m: "Migracion", texto: str) -> bool:
    """¿El archivo se registra solo? Lanza ValueError si inserta una fila con
    OTRO nombre que el suyo: un archivo renumerado (aquí se renumera seguido:
    0058→0059, 0061→0062, y la limpieza y la cuarentena ya van por la tercera
    numeración) que no cambió su insert. Se revisa ANTES de tocar la base, en
    --plan también; `ejecutar` lo vuelve a medir contra la base."""
    ajenos = [n for n in registros_propios(texto) if n != m.nombre]
    if ajenos:
        raise ValueError(f"{m.ruta.name} se registra en ops.migraciones con OTRO nombre ({', '.join(ajenos)}): "
                         "¿lo renumeraron sin cambiar su insert? Corrige el nombre en el archivo: el registro es "
                         "de solo agregar y esa fila movería la frontera para siempre.")
    return bool(registros_propios(texto))


# ═══════════════════════════════════════════════════════════════════════════
# El plan (función pura: se prueba sin base)
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class Plan:
    registro_existe: bool
    recrear: bool = False
    frontera: int | None = None
    saltar: list[Migracion] = field(default_factory=list)       # ya tienen fila
    correr: list[Migracion] = field(default_factory=list)       # se ejecutan y registran
    adoptar: list[Migracion] = field(default_factory=list)      # se registran SIN ejecutar
    excluidas: list[Migracion] = field(default_factory=list)    # --excepto: ni se adoptan ni corren
    atrasadas: list[Migracion] = field(default_factory=list)    # pendientes ≤ frontera
    huerfanas: list[str] = field(default_factory=list)          # filas sin archivo (informativo)
    sin_huella: dict[str, list[str]] = field(default_factory=dict)  # por adoptar, con lo que falta
    sin_huella_ok: list[str] = field(default_factory=list)
    reemplazadas: list[str] = field(default_factory=list)       # por adoptar, sin efecto propio al final
    adoptar_hasta: int | None = None
    hasta: int | None = None                                    # --hasta: lo de arriba no corre hoy
    pospuestas: list[Migracion] = field(default_factory=list)   # pendientes > hasta (siguen pendientes)
    base_con_tablas: bool = False                               # sin registro, pero NO vacía
    bloqueo: str | None = None


def _nombres(lista: str | None) -> list[str]:
    return [x.strip().removesuffix(".sql") for x in (lista or "").split(",") if x.strip()]


def armar_plan(migraciones: list[Migracion], registro: dict[str, int] | None, *,
               adoptar_hasta: int | None = None, excepto: list[str] | tuple = (),
               incluir_atrasadas: bool = False, recrear: bool = False,
               base_con_tablas: bool = False, hasta: int | None = None) -> Plan:
    """Decide qué se salta, qué corre y qué se adopta.

    `registro` es {migracion: filas} de ops.migraciones, o None si la tabla no
    existe. Con `recrear` la base se tira: se ignora el registro y corre todo.
    Sin registro, `base_con_tablas` distingue una base nueva (corre todo) de una
    existente sin registro (se detiene: re-correr desde la 0001 encima de lo
    vivo no es seguro). `hasta` (--hasta NNNN) corre solo las pendientes <=
    NNNN: las de arriba quedan `pospuestas` y la próxima corrida las toma (p. ej.
    el 9-oct el sandbox recibió la 0068 y la 0069, y no la 0070). Lanza
    ValueError ante una petición incoherente.
    """
    if hasta is not None and (recrear or adoptar_hasta is not None):
        raise ValueError("--hasta solo va al correr lo pendiente: no con --recrear (corre todo) ni con "
                         "--adoptar-hasta (que no corre nada)")
    if recrear:
        if adoptar_hasta is not None:
            raise ValueError("--adoptar-hasta no va con --recrear: una base recreada corre todo y lo registra")
        return Plan(registro_existe=False, recrear=True, correr=list(migraciones))
    if registro is None:
        if adoptar_hasta is not None:
            raise ValueError("no existe ops.migraciones: no hay frontera contra la cual adoptar. Una base "
                             "nueva se arma sin --adoptar-hasta (corre todo y lo registra)")
        if base_con_tablas:
            return Plan(registro_existe=False, base_con_tablas=True, bloqueo=(
                "la base YA tiene tablas en los esquemas propios pero no tiene ops.migraciones (una corrida "
                "que quedó a medias antes de la 0064, o una restauración anterior al 6-oct). Re-correr desde la "
                "0001 encima de lo vivo no es seguro: la 0033 vieja truena contra la forma de la 0036 "
                "(stock_texco) y, tras mudar tablas, recrearía copias vacías. Si la base es desechable: "
                "--recrear. Si no: crear el registro aplicando la 0064 con su acta y luego --adoptar-hasta."))
        return _recortar(Plan(registro_existe=False, correr=list(migraciones)), hasta)

    nombres_archivo = {m.nombre for m in migraciones}
    frontera = max((int(n[:4]) for n in registro if n[:4].isdigit()), default=None)
    plan = Plan(registro_existe=True, frontera=frontera, adoptar_hasta=adoptar_hasta,
                huerfanas=sorted(n for n in registro if n not in nombres_archivo))
    pendientes = []
    for m in migraciones:
        (plan.saltar if registro.get(m.nombre, 0) > 0 else pendientes).append(m)

    if adoptar_hasta is not None:
        if frontera is None:
            raise ValueError("ops.migraciones está vacía: no hay frontera contra la cual adoptar")
        if adoptar_hasta > frontera:
            raise ValueError(f"--adoptar-hasta {adoptar_hasta:04d} pasa la frontera {frontera:04d}: solo se "
                             "adopta por debajo de lo ya registrado (lo de arriba nunca corrió)")
        candidatas = [m for m in pendientes if m.numero <= adoptar_hasta]
        fuera = set(excepto)
        desconocidas = sorted(fuera - {m.nombre for m in candidatas})
        if desconocidas:
            raise ValueError(f"--excepto nombra lo que no está por adoptar: {', '.join(desconocidas)}")
        plan.adoptar = [m for m in candidatas if m.nombre not in fuera]
        plan.excluidas = [m for m in candidatas if m.nombre in fuera]
        return plan

    if excepto:
        raise ValueError("--excepto solo va con --adoptar-hasta")
    plan.atrasadas = [m for m in pendientes if frontera is not None and m.numero <= frontera]
    plan.correr = list(pendientes)
    if plan.atrasadas and not incluir_atrasadas:
        plan.bloqueo = (
            f"hay {len(plan.atrasadas)} pendiente(s) por DEBAJO de la frontera {frontera:04d}: "
            f"{', '.join(m.nombre for m in plan.atrasadas)}. O ya están aplicadas (adóptalas: "
            "--adoptar-hasta NNNN) o hay que correrlas (--incluir-atrasadas). No se adivina.")
    return _recortar(plan, hasta)


def _recortar(plan: Plan, hasta: int | None) -> Plan:
    """--hasta NNNN: corre solo lo pendiente <= NNNN; lo de arriba queda
    pospuesto (sin fila: la próxima corrida lo toma). Las atrasadas siguen
    bloqueando aunque queden arriba de NNNN: --hasta no es para saltarlas."""
    if hasta is None:
        return plan
    plan.hasta = hasta
    plan.pospuestas = [m for m in plan.correr if m.numero > hasta]
    plan.correr = [m for m in plan.correr if m.numero <= hasta]
    return plan


def aplicar_huellas(plan: Plan, faltantes: dict[str, list[str]], sin_huella_ok: list[str],
                    reemplazadas: list[str] | tuple = ()) -> None:
    """Bloquea la adopción si a alguna por adoptar le falta su huella en la base
    (o no tiene NADA verificable) y no la nombraron en --sin-huella-ok (las de
    --excepto ya no están aquí). `reemplazadas` solo se informa."""
    por_adoptar = {m.nombre for m in plan.adoptar}
    desconocidas = sorted(set(sin_huella_ok) - por_adoptar)
    if desconocidas:
        raise ValueError(f"--sin-huella-ok nombra lo que no está por adoptar: {', '.join(desconocidas)}")
    plan.sin_huella = {n: f for n, f in faltantes.items() if n in por_adoptar}
    plan.sin_huella_ok = sorted(set(sin_huella_ok))
    plan.reemplazadas = [n for n in reemplazadas if n in por_adoptar]
    duras = [n for n in plan.sin_huella if n not in plan.sin_huella_ok]
    if duras:
        plan.bloqueo = (
            f"{len(duras)} migración(es) por adoptar no dejan huella en la base (o no tienen nada "
            f"verificable): {', '.join(duras)}. Adoptarlas las daría por aplicadas sin estarlo, y el registro "
            "es de solo agregar. Nómbralas en --excepto (se corren después con --incluir-atrasadas) o en "
            "--sin-huella-ok (si lo que falta lo quitó algo fuera de main, o si consta que sí se aplicaron).")


# ═══════════════════════════════════════════════════════════════════════════
# La huella de cada migración: lo que deja y sigue vivo al final de la cadena
# ═══════════════════════════════════════════════════════════════════════════
# Por qué tantas clases de huella (revisión del 8-oct-2026): con solo tablas,
# vistas y columnas, una migración cuyo efecto es RLS, security_invoker, una
# función, un índice o un renombre tenía la huella VACÍA, pasaba sin comparar
# nada y se adoptaba. En el sandbox eso daba por aplicadas la 0043, la
# 0044_blindaje, la 0045 y la 0050, que NO lo están — y como ops.migraciones es
# de solo agregar, la fila mentiría para siempre. Ahora cada migración responde
# por lo último que deja en pie de cada clase. Una que sí hace algo verificable
# pero que la cadena reemplazó entero queda «reemplazada» (adoptarla no cambia
# nada); una que no hace nada verificable (solo grants, comments, datos)
# BLOQUEA salvo que se nombre en --excepto o en --sin-huella-ok.
#
# Lo que NO ve: el CUERPO de una vista (un `create or replace view` solo se
# comprueba por existencia e invoker), grants y revokes, comments, datos, tipos
# y defaults de columnas.
def _modelo_rls():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import verificar_rls  # noqa: E402  (mismo directorio; solo biblioteca estándar)
    return verificar_rls


_ID = r"[a-z_][a-z0-9_]*"
_ALTER = re.compile(rf"\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?({_ID})\.({_ID})\s+(.*?);", re.I | re.S)
_ADD_COL = re.compile(rf"\badd\s+column\s+(?:if\s+not\s+exists\s+)?({_ID})", re.I)
_DROP_COL = re.compile(rf"\bdrop\s+column\s+(?:if\s+exists\s+)?({_ID})", re.I)
# `rename [column] a to b`. No confunde `rename to x` (la tabla) ni `rename constraint`.
_REN_COL = re.compile(rf"\brename\s+(?:column\s+)?({_ID})\s+to\s+({_ID})", re.I)
_ADD_CON = re.compile(rf"\badd\s+constraint\s+({_ID})", re.I)
_DROP_CON = re.compile(rf"\bdrop\s+constraint\s+(?:if\s+exists\s+)?({_ID})", re.I)
_REN_CON = re.compile(rf"\brename\s+constraint\s+({_ID})\s+to\s+({_ID})", re.I)
_FUNCION = re.compile(rf"\bcreate\s+(?:or\s+replace\s+)?function\s+({_ID})\.({_ID})\s*\(", re.I)
_TIRAR_FUNCION = re.compile(rf"\bdrop\s+function\s+(?:if\s+exists\s+)?({_ID})\.({_ID})\s*(\()?", re.I)
_CUERPO = re.compile(r"\bas\s+(\$(?:[a-z_][a-z0-9_]*)?\$)", re.I)
_INDICE = re.compile(rf"\bcreate\s+(?:unique\s+)?index\s+(?:concurrently\s+)?(?:if\s+not\s+exists\s+)?"
                     rf"({_ID})\s+on\s+(?:only\s+)?({_ID})\.({_ID})", re.I)
_TIRAR_INDICE = re.compile(rf"\bdrop\s+index\s+(?:concurrently\s+)?(?:if\s+exists\s+)?(?:({_ID})\.)?({_ID})", re.I)
_REN_INDICE = re.compile(rf"\balter\s+index\s+(?:if\s+exists\s+)?(?:({_ID})\.)?({_ID})\s+rename\s+to\s+({_ID})", re.I)
_TRIGGER = re.compile(rf"\bcreate\s+(?:or\s+replace\s+)?(?:constraint\s+)?trigger\s+({_ID})\s[^;]*?"
                      rf"\bon\s+({_ID})\.({_ID})", re.I)
_TIRAR_TRIGGER = re.compile(rf"\bdrop\s+trigger\s+(?:if\s+exists\s+)?({_ID})\s+on\s+({_ID})\.({_ID})", re.I)
_POLITICA = re.compile(rf"\bcreate\s+policy\s+({_ID})\s+on\s+({_ID})\.({_ID})", re.I)
_TIRAR_POLITICA = re.compile(rf"\bdrop\s+policy\s+(?:if\s+exists\s+)?({_ID})\s+on\s+({_ID})\.({_ID})", re.I)
_ESQUEMA = re.compile(rf"\bcreate\s+schema\s+(?:if\s+not\s+exists\s+)?({_ID})", re.I)
# Lo que una migración toca sin crearlo: alter/grant/revoke/comment sobre s.t.
# `alter … if exists` no cuenta (no truena si falta).
_TOCA = re.compile(rf"\b(?:alter\s+(?:table|view)\s+(?!if\s+exists\b)(?:only\s+)?"
                   rf"|(?:grant|revoke)\s+[^;]*?\bon\s+(?:table\s+)?"
                   rf"|comment\s+on\s+(?:table|view|column)\s+)({_ID})\.({_ID})", re.I)

CLASES = ("relaciones", "columnas", "rls", "invoker", "funciones", "indices", "triggers",
          "restricciones", "politicas", "esquemas", "ausentes")


def _vacia() -> dict:
    h: dict = {c: set() for c in CLASES}
    h["funciones"] = {}       # (s, f, nargs) → cuerpo normalizado (o None si no va entre $…$)
    h["sentencias"] = 0       # sentencias verificables que trae el archivo
    return h


def _normal(cuerpo: str, rls=None) -> str:
    """El cuerpo de una función sin comentarios y con el espacio colapsado (igual
    para el archivo y para pg_proc.prosrc, que Postgres guarda tal cual)."""
    rls = rls or _modelo_rls()
    return " ".join(rls._sin_comentarios(cuerpo).split())


def _entre_parentesis(sql: str, i: int) -> tuple[str, int]:
    """El texto desde sql[i] hasta el ')' que cierra el '(' de sql[i-1]."""
    nivel, j, n = 1, i, len(sql)
    while j < n:
        c = sql[j]
        if c == "'":
            k = j + 1
            while k < n and not (sql[k] == "'" and sql[k + 1:k + 2] != "'"):
                k += 2 if sql[k:k + 2] == "''" else 1
            j = k
        elif c == "(":
            nivel += 1
        elif c == ")":
            nivel -= 1
            if nivel == 0:
                return sql[i:j], j + 1
        j += 1
    return sql[i:], n


def _num_args(args: str) -> int:
    """pronargs: los argumentos de entrada (IN, INOUT, VARIADIC; no los OUT)."""
    partes, actual, nivel, enc = [], [], 0, False
    for c in args:
        if c == "'":
            enc = not enc
        elif not enc and c == "(":
            nivel += 1
        elif not enc and c == ")":
            nivel -= 1
        if c == "," and nivel == 0 and not enc:
            partes.append("".join(actual))
            actual = []
        else:
            actual.append(c)
    partes.append("".join(actual))
    return sum(1 for p in partes if p.strip() and not re.match(r"out\s", p.strip(), re.I))


def _funciones(sql: str) -> list[tuple[int, str, tuple, str | None]]:
    """(posición, '+'/'-', (s, f, nargs|None), cuerpo|None) de cada create/drop function."""
    ev = []
    for x in _FUNCION.finditer(sql):
        args, fin = _entre_parentesis(sql, x.end())
        cuerpo = None
        c = _CUERPO.search(sql, fin)
        if c and ";" not in sql[fin:c.start()]:
            cierre = sql.find(c.group(1), c.end())
            if cierre != -1:
                cuerpo = sql[c.end():cierre]
        ev.append((x.start(), "+", (x.group(1).lower(), x.group(2).lower(), _num_args(args)), cuerpo))
    for x in _TIRAR_FUNCION.finditer(sql):
        n = _num_args(_entre_parentesis(sql, x.end())[0]) if x.group(3) else None
        ev.append((x.start(), "-", (x.group(1).lower(), x.group(2).lower(), n), None))
    return sorted(ev, key=lambda e: e[0])


def huellas_esperadas(migraciones: list[Migracion]) -> dict[str, dict]:
    """Por migración, lo que deja en pie al final de TODA la cadena (con el modelo
    de verificar_rls: drops, renombres, `set schema`, `alter schema … rename`):
      relaciones (s, t) · columnas (s, t, c) que agrega o renombra · rls (s, t) ·
      invoker (s, v) · funciones {(s, f, nargs): cuerpo} · indices (s, t, ix) ·
      triggers / restricciones / politicas (s, t, nombre) · esquemas s ·
      ausentes: lo que tira y nadie vuelve a crear (('rel', s, t),
      ('fn', s, f, nargs|None), ('col', s, t, c)).
    Cada objeto lo responde la ÚLTIMA migración que lo dejó como está: si una
    posterior tira y recrea la tabla, lo de la anterior ya no cuenta. Es una
    sonda: lo que tira una migración que no está en main (p. ej. la 0052)
    aparece como faltante, y por eso existe --sin-huella-ok."""
    rls = _modelo_rls()
    modelo = rls.Esquema()
    out = {m.nombre: _vacia() for m in migraciones}
    por_archivo = {m.ruta.name: m.nombre for m in migraciones}
    funciones: dict[tuple, tuple[str, str | None]] = {}
    tiradas_fn: dict[tuple, str] = {}
    tiradas_rel: dict[tuple, str] = {}
    esquemas: dict[str, str] = {}

    def objeto(s: str, t: str):
        return modelo.tablas.get((s, t)) or modelo.vistas.get((s, t))

    def objetos():
        return list(modelo.tablas.values()) + list(modelo.vistas.values())

    for m in migraciones:
        texto = m.ruta.read_text(encoding="utf-8")
        sql = rls._sin_comentarios(texto)
        h = out[m.nombre]
        h["sentencias"] = sum(len(r.findall(sql)) for r in (
            rls._CREAR_TABLA, rls._CREAR_VISTA, rls._TIRAR_TABLA, rls._TIRAR_VISTA, rls._RLS, rls._INVOKER,
            _ADD_COL, _DROP_COL, _REN_COL, _ADD_CON, _FUNCION, _TIRAR_FUNCION, _INDICE, _TRIGGER,
            _POLITICA, _ESQUEMA))

        # Lo que no cuelga de una tabla, en el orden del archivo.
        for _, signo, clave, cuerpo in _funciones(sql):
            if signo == "+":
                funciones[clave] = (m.nombre, None if cuerpo is None else _normal(cuerpo, rls))
            else:
                s, f, n = clave
                for k in [k for k in funciones if k[:2] == (s, f) and (n is None or k[2] == n)]:
                    del funciones[k]
                tiradas_fn[clave] = m.nombre
        # Un esquema lo responde quien lo CREÓ: el `create schema if not exists`
        # de una posterior no hace nada si ya existe (la 0068, 0069 y 0070 lo
        # repiten «para que el sandbox no dependa del orden»; con «gana la última»
        # la 0066 se quedaba sin huella). Si se tiró, el que lo vuelve a crear manda.
        for x in _ESQUEMA.finditer(sql):
            esquemas.setdefault(x.group(1).lower(), m.nombre)
        for viejo, nuevo in rls._RENOMBRAR_ESQ.findall(sql):
            if viejo.lower() in esquemas:
                esquemas[nuevo.lower()] = esquemas.pop(viejo.lower())
        for s in rls._TIRAR_ESQ.findall(sql):
            esquemas.pop(s.lower(), None)
        for r in (rls._TIRAR_TABLA, rls._TIRAR_VISTA):
            for s, t in r.findall(sql):
                tiradas_rel[(s.lower(), t.lower())] = m.nombre

        modelo.aplicar(texto, m.ruta.name)

        # Lo que cuelga de una tabla se ata al OBJETO del modelo tras el archivo:
        # si una migración posterior la tira y la recrea, es otro objeto.
        for a in _ALTER.finditer(sql):
            o = objeto(a.group(1).lower(), a.group(2).lower())
            if o is None:
                continue
            cols, sin_cols, cons = o.setdefault("_cols", {}), o.setdefault("_sin_cols", {}), o.setdefault("_cons", {})
            cuerpo = a.group(3)
            sub = sorted([(x.start(), "+c", x.groups()) for x in _ADD_COL.finditer(cuerpo)]
                         + [(x.start(), "-c", x.groups()) for x in _DROP_COL.finditer(cuerpo)]
                         + [(x.start(), "rc", x.groups()) for x in _REN_COL.finditer(cuerpo)]
                         + [(x.start(), "+k", x.groups()) for x in _ADD_CON.finditer(cuerpo)]
                         + [(x.start(), "-k", x.groups()) for x in _DROP_CON.finditer(cuerpo)]
                         + [(x.start(), "rk", x.groups()) for x in _REN_CON.finditer(cuerpo)],
                         key=lambda e: e[0])
            for _, tipo, g in sub:
                g = tuple(x.lower() for x in g)
                if tipo == "+c":
                    cols[g[0]] = m.nombre
                    sin_cols.pop(g[0], None)
                elif tipo == "-c":
                    cols.pop(g[0], None)
                    sin_cols[g[0]] = m.nombre
                elif tipo == "rc":
                    cols.pop(g[0], None)
                    cols[g[1]] = m.nombre
                elif tipo == "+k":
                    cons[g[0]] = m.nombre
                elif tipo == "-k":
                    cons.pop(g[0], None)
                else:
                    cons.pop(g[0], None)
                    cons[g[1]] = m.nombre
        # En el orden del archivo: la 0049 renombra los índices viejos y luego
        # crea los nuevos con los MISMOS nombres sobre la tabla nueva.
        for x in sorted(list(_INDICE.finditer(sql)) + list(_TIRAR_INDICE.finditer(sql))
                        + list(_REN_INDICE.finditer(sql)), key=lambda x: x.start()):
            if x.re is _INDICE:
                o = objeto(x.group(2).lower(), x.group(3).lower())
                if o is not None:
                    o.setdefault("_idx", {})[x.group(1).lower()] = m.nombre
                continue
            nombre = x.group(2).lower()
            for o in objetos():
                idx = o.get("_idx", {})
                if nombre in idx:
                    idx.pop(nombre)
                    if x.re is _REN_INDICE:
                        idx[x.group(3).lower()] = m.nombre
        for crear, tirar, llave in ((_TRIGGER, _TIRAR_TRIGGER, "_trg"), (_POLITICA, _TIRAR_POLITICA, "_pol")):
            for x in sorted(list(crear.finditer(sql)) + list(tirar.finditer(sql)), key=lambda x: x.start()):
                o = objeto(x.group(2).lower(), x.group(3).lower())
                if o is None:
                    continue
                if x.re is crear:
                    o.setdefault(llave, {})[x.group(1).lower()] = m.nombre
                else:
                    o.setdefault(llave, {}).pop(x.group(1).lower(), None)
        # RLS / security_invoker: responde quien dejó el candado como está.
        for o in objetos():
            if "_prot" not in o or o["_prot"] != o["protegido"]:
                o["_prot"], o["_prot_dueno"] = o["protegido"], m.nombre

    for coleccion, clase in ((modelo.tablas, "rls"), (modelo.vistas, "invoker")):
        for (s, t), o in coleccion.items():
            dueno = por_archivo.get(o["origen"])
            if dueno:
                out[dueno]["relaciones"].add((s, t))
            if o["protegido"] and o.get("_prot_dueno") in out:
                out[o["_prot_dueno"]][clase].add((s, t))
            for c, d in o.get("_cols", {}).items():
                out[d]["columnas"].add((s, t, c))
            for c, d in o.get("_sin_cols", {}).items():
                out[d]["ausentes"].add(("col", s, t, c))
            for ix, d in o.get("_idx", {}).items():
                out[d]["indices"].add((s, t, ix))
            for llave, clase_h in (("_trg", "triggers"), ("_cons", "restricciones"), ("_pol", "politicas")):
                for nombre, d in o.get(llave, {}).items():
                    out[d][clase_h].add((s, t, nombre))
    vivas = set(modelo.tablas) | set(modelo.vistas)
    for k, d in tiradas_rel.items():
        if k not in vivas:
            out[d]["ausentes"].add(("rel",) + k)
    for k, (d, cuerpo) in funciones.items():
        out[d]["funciones"][k] = cuerpo
    for (s, f, n), d in tiradas_fn.items():
        if not any(k[:2] == (s, f) and (n is None or k[2] == n) for k in funciones):
            out[d]["ausentes"].add(("fn", s, f, n))
    for s, d in esquemas.items():
        out[d]["esquemas"].add(s)
    return out


def _tiene_huella(e: dict) -> bool:
    return any(e[c] for c in CLASES)


def esquemas_de(esperadas: dict[str, dict]) -> list[str]:
    """Los esquemas que hay que leer del catálogo para comparar."""
    out = set()
    for e in esperadas.values():
        for c in CLASES:
            for k in e[c]:
                if isinstance(k, str):
                    out.add(k)
                else:
                    out.add(k[1] if k[0] in ("rel", "fn", "col") else k[0])
    return sorted(out)


def reemplazadas(esperadas: dict[str, dict], nombres: list[str]) -> list[str]:
    """Las que sí hacen algo verificable pero la cadena lo reemplazó entero:
    adoptarlas no cambia nada en la base."""
    return [n for n in nombres if n in esperadas and not _tiene_huella(esperadas[n])
            and esperadas[n]["sentencias"] > 0]


@dataclass
class Catalogo:
    """Lo que la base tiene de verdad (en los esquemas que se leen)."""
    relaciones: set = field(default_factory=set)    # (s, t): tablas, vistas, matviews, foráneas
    columnas: set = field(default_factory=set)      # (s, t, c)
    rls: set = field(default_factory=set)           # (s, t) con relrowsecurity
    invoker: set = field(default_factory=set)       # (s, v) con security_invoker on
    funciones: dict = field(default_factory=dict)   # (s, f, nargs) → [cuerpo normalizado, …]
    indices: set = field(default_factory=set)       # (s, t, ix)
    triggers: set = field(default_factory=set)      # (s, t, nombre)
    restricciones: set = field(default_factory=set)
    politicas: set = field(default_factory=set)
    esquemas: set = field(default_factory=set)


SIN_HUELLA = "(sin huella verificable: solo grants, comments o datos)"


def faltantes_en_base(esperadas: dict[str, dict], cat: Catalogo, nombres: list[str]) -> dict[str, list[str]]:
    """Lo que falta (o sobra) en la base, por migración (solo las de `nombres`).
    Lo que cuelga de una tabla solo se pide si la tabla existe: si falta la
    tabla, ya lo dice la migración que la crea."""
    out = {}
    for n in nombres:
        e = esperadas.get(n) or _vacia()
        rel = cat.relaciones
        falta = [f"{s}.{t}" for s, t in sorted(e["relaciones"]) if (s, t) not in rel]
        falta += [f"{s}.{t}.{c}" for s, t, c in sorted(e["columnas"]) if (s, t) in rel and (s, t, c) not in cat.columnas]
        falta += [f"rls {s}.{t}" for s, t in sorted(e["rls"]) if (s, t) in rel and (s, t) not in cat.rls]
        falta += [f"invoker {s}.{t}" for s, t in sorted(e["invoker"]) if (s, t) in rel and (s, t) not in cat.invoker]
        for (s, f, k), cuerpo in sorted(e["funciones"].items()):
            vivos = cat.funciones.get((s, f, k))
            if vivos is None:
                falta.append(f"fn {s}.{f}/{k}")
            elif cuerpo is not None and cuerpo not in vivos:
                falta.append(f"fn {s}.{f}/{k} (otro cuerpo)")
        for clase, etiqueta, vivos in (("indices", "índice", cat.indices), ("triggers", "trigger", cat.triggers),
                                       ("restricciones", "constraint", cat.restricciones),
                                       ("politicas", "policy", cat.politicas)):
            falta += [f"{etiqueta} {s}.{t}.{x}" for s, t, x in sorted(e[clase])
                      if (s, t) in rel and (s, t, x) not in vivos]
        falta += [f"esquema {s}" for s in sorted(e["esquemas"]) if s not in cat.esquemas]
        for a in sorted(e["ausentes"], key=str):
            if a[0] == "rel" and (a[1], a[2]) in rel:
                falta.append(f"sigue viva {a[1]}.{a[2]}")
            elif a[0] == "col" and (a[1], a[2], a[3]) in cat.columnas:
                falta.append(f"sigue viva {a[1]}.{a[2]}.{a[3]}")
            elif a[0] == "fn" and any(k[:2] == (a[1], a[2]) and (a[3] is None or k[2] == a[3]) for k in cat.funciones):
                falta.append(f"sigue viva fn {a[1]}.{a[2]}/{'*' if a[3] is None else a[3]}")
        if not _tiene_huella(e) and not e["sentencias"]:
            falta = [SIN_HUELLA]
        if falta:
            out[n] = falta
    return out


def requisitos_externos(migraciones: list[Migracion]) -> dict[tuple[str, str], str]:
    """Lo que la cadena TOCA (alter / grant / revoke / comment) sin que ninguna
    migración lo cree antes: {(esquema, relación): la primera que lo necesita}.
    En una base recreada eso tiene que existir ya, o la cadena truena a la mitad
    (hoy: la 0025 sobre propuestas_retirado.* y public.packing_*)."""
    rls = _modelo_rls()
    modelo = rls.Esquema()
    vistos: set = set()
    req: dict[tuple[str, str], str] = {}
    for m in migraciones:
        texto = m.ruta.read_text(encoding="utf-8")
        sql = rls._sin_comentarios(texto)
        vistos |= set(modelo.tablas) | set(modelo.vistas)
        vistos |= {(s.lower(), t.lower()) for s, t in rls._CREAR_TABLA.findall(sql)}
        vistos |= {(g[1].lower(), g[2].lower()) for g in rls._CREAR_VISTA.findall(sql)}
        vistos |= {(s.lower(), nuevo.lower()) for s, _, nuevo in rls._RENOMBRAR_OBJ.findall(sql)}
        vistos |= {(destino.lower(), t.lower()) for _, t, destino in rls._MUDAR_OBJ.findall(sql)}
        for x in _TOCA.finditer(sql):
            k = (x.group(1).lower(), x.group(2).lower())
            if k not in vistos:
                req.setdefault(k, m.nombre)
        modelo.aplicar(texto, m.ruta.name)
    return req


# ═══════════════════════════════════════════════════════════════════════════
# La base
# ═══════════════════════════════════════════════════════════════════════════
def conectar(url: str):
    import psycopg2
    pg = psycopg2.connect(url, connect_timeout=20)
    pg.autocommit = False
    return pg


def leer_registro(cur) -> dict[str, int] | None:
    cur.execute("select to_regclass('ops.migraciones') is not null")
    if not cur.fetchone()[0]:
        return None
    cur.execute("select migracion, count(*) from ops.migraciones group by 1")
    return {m: int(n) for m, n in cur.fetchall()}


# Cada consulta al catálogo lleva su etiqueta (`-- catalogo:<clase>`): así la
# reconoce la base falsa de las pruebas.
_SQL_CATALOGO = {
    "relaciones": """select n.nspname, c.relname, c.relrowsecurity,
                            coalesce(array_to_string(c.reloptions, ','), '')
                       from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid = c.relnamespace
                      where c.relkind in ('r', 'p', 'v', 'm', 'f') and n.nspname = any(%s)""",
    "columnas": """select n.nspname, c.relname, a.attname from pg_catalog.pg_attribute a
                     join pg_catalog.pg_class c on c.oid = a.attrelid
                     join pg_catalog.pg_namespace n on n.oid = c.relnamespace
                    where a.attnum > 0 and not a.attisdropped and n.nspname = any(%s)""",
    "funciones": """select n.nspname, p.proname, p.pronargs, p.prosrc from pg_catalog.pg_proc p
                      join pg_catalog.pg_namespace n on n.oid = p.pronamespace where n.nspname = any(%s)""",
    "indices": """select n.nspname, t.relname, i.relname from pg_catalog.pg_index x
                    join pg_catalog.pg_class i on i.oid = x.indexrelid
                    join pg_catalog.pg_class t on t.oid = x.indrelid
                    join pg_catalog.pg_namespace n on n.oid = t.relnamespace where n.nspname = any(%s)""",
    "triggers": """select n.nspname, c.relname, g.tgname from pg_catalog.pg_trigger g
                     join pg_catalog.pg_class c on c.oid = g.tgrelid
                     join pg_catalog.pg_namespace n on n.oid = c.relnamespace
                    where not g.tgisinternal and n.nspname = any(%s)""",
    "restricciones": """select n.nspname, c.relname, k.conname from pg_catalog.pg_constraint k
                          join pg_catalog.pg_class c on c.oid = k.conrelid
                          join pg_catalog.pg_namespace n on n.oid = c.relnamespace where n.nspname = any(%s)""",
    "politicas": """select n.nspname, c.relname, p.polname from pg_catalog.pg_policy p
                      join pg_catalog.pg_class c on c.oid = p.polrelid
                      join pg_catalog.pg_namespace n on n.oid = c.relnamespace where n.nspname = any(%s)""",
    "esquemas": "select nspname from pg_catalog.pg_namespace where nspname = any(%s)",
}
_INVOKER_ON = {"security_invoker=on", "security_invoker=true", "security_invoker=1", "security_invoker=yes"}


def _catalogo(cur, clase: str, params) -> list[tuple]:
    cur.execute(f"-- catalogo:{clase}\n{_SQL_CATALOGO[clase]}", (params,))
    return cur.fetchall()


def leer_catalogo(cur, esquemas: list[str]) -> Catalogo:
    rls = _modelo_rls()
    cat = Catalogo()
    for s, t, seguridad, opciones in _catalogo(cur, "relaciones", esquemas):
        cat.relaciones.add((s, t))
        if seguridad:
            cat.rls.add((s, t))
        if _INVOKER_ON & {o.strip().lower() for o in (opciones or "").split(",")}:
            cat.invoker.add((s, t))
    cat.columnas = {tuple(r) for r in _catalogo(cur, "columnas", esquemas)}
    for s, f, n, cuerpo in _catalogo(cur, "funciones", esquemas):
        cat.funciones.setdefault((s, f, int(n)), []).append(_normal(cuerpo or "", rls))
    cat.indices = {tuple(r) for r in _catalogo(cur, "indices", esquemas)}
    cat.triggers = {tuple(r) for r in _catalogo(cur, "triggers", esquemas)}
    cat.restricciones = {tuple(r) for r in _catalogo(cur, "restricciones", esquemas)}
    cat.politicas = {tuple(r) for r in _catalogo(cur, "politicas", esquemas)}
    cat.esquemas = {r[0] for r in _catalogo(cur, "esquemas", esquemas)}
    return cat


def faltan_requisitos(cur, req: dict[tuple[str, str], str]) -> list[str]:
    """De lo que la cadena toca sin crear, lo que esta base NO tiene. Lo de los
    esquemas propios falta siempre después de --recrear (se tiran)."""
    externos = sorted(k for k in req if k[0] not in ESQUEMAS_PROPIOS)
    hay = set()
    if externos:
        cur.execute("-- catalogo:requisitos\nselect r, to_regclass(r) is not null from unnest(%s::text[]) as r",
                    ([f"{s}.{t}" for s, t in externos],))
        hay = {r for r, existe in cur.fetchall() if existe}
    return [f"{s}.{t} ({req[(s, t)]})" for s, t in sorted(req, key=lambda k: (req[k], k))
            if s in ESQUEMAS_PROPIOS or f"{s}.{t}" not in hay]


def base_con_tablas(cur) -> bool:
    """¿Hay tablas en los esquemas propios? (sin ops.migraciones, eso no es una base nueva)"""
    cur.execute("-- catalogo:propias\nselect count(*) from pg_catalog.pg_class c "
                "join pg_catalog.pg_namespace n on n.oid = c.relnamespace "
                "where c.relkind in ('r', 'p') and n.nspname = any(%s)", (list(ESQUEMAS_PROPIOS),))
    return int(cur.fetchone()[0]) > 0


def registrar(cur, m: Migracion, modo: str, texto: str, **extra) -> None:
    """Una fila en ops.migraciones. `quien` queda con su default de la 0064
    (app.usuario o current_user); el runner firma en detalle.por."""
    detalle = {"por": POR, "modo": modo, "sha256": huella_sha(texto), **extra}
    cur.execute("insert into ops.migraciones (migracion, detalle) values (%s, %s::jsonb)",
                (m.nombre, json.dumps(detalle, ensure_ascii=False)))


class FalloMigracion(RuntimeError):
    """`sin_registro`: el fallo llegó cuando ops.migraciones todavía no existía
    (base nueva antes de la 0064): lo confirmado antes NO tiene fila."""

    def __init__(self, m: Migracion, exc: Exception, hechas: list[str], sin_registro: bool = False):
        super().__init__(f"FALLÓ {m.ruta.name}: {exc}")
        self.migracion, self.exc, self.hechas, self.sin_registro = m, exc, hechas, sin_registro


def ejecutar(pg, plan: Plan, eco=print) -> list[str]:
    """Corre plan.correr, una transacción por archivo con su fila. Devuelve lo
    que quedó confirmado. Ante un fallo deshace ESE archivo y lanza FalloMigracion.

    Si el archivo inserta filas en ops.migraciones con OTRO nombre que el suyo
    (p. ej. lo renumeraron y su `insert … values ('NNNN_…')` quedó con el número
    viejo), se deshace entero: esa fila fantasma movería la frontera y, como el
    registro es de solo agregar, ya no se podría borrar."""
    cur = pg.cursor()
    sin_fila: list[tuple[Migracion, str]] = []   # corrieron antes de existir ops.migraciones
    hechas: list[str] = []
    hay_registro = plan.registro_existe
    for m in plan.correr:
        crudo = m.ruta.read_text(encoding="utf-8")
        try:
            sql = en_una_transaccion(crudo, m.ruta.name)
            revisar_registro_propio(m, crudo)
            cur.execute("select pg_advisory_xact_lock(%s)", (CANDADO,))
            reg_antes = leer_registro(cur)
            antes = None if reg_antes is None else reg_antes.get(m.nombre, 0)
            if antes:   # otra corrida la registró después de armar el plan
                pg.rollback()
                eco(f"Salto {m.ruta.name}: ya la registró otra corrida.")
                continue
            eco(f"Aplicando {m.ruta.name}…")
            cur.execute(sql)
            for aviso in pg.notices:     # los `raise notice` del archivo
                eco(f"  {aviso.strip()}")
            del pg.notices[:]
            reg_despues = leer_registro(cur)
            despues = None if reg_despues is None else reg_despues.get(m.nombre, 0)
            if reg_despues is None:
                sin_fila.append((m, crudo))
            else:
                ajenas = sorted(n for n, k in reg_despues.items()
                                if n != m.nombre and k > (reg_antes or {}).get(n, 0))
                if ajenas:
                    raise ValueError(f"{m.ruta.name} insertó en ops.migraciones filas con OTRO nombre "
                                     f"({', '.join(ajenas)}): ¿lo renumeraron sin cambiar su insert? "
                                     "Se deshace entero; corrige el nombre en el archivo.")
                # ops.migraciones NO tiene un único sobre `migracion` (ni la 0064 ni
                # producción, medido el 9-oct): nada en la base impide una fila doble.
                # El que no duplica es el runner. Un archivo que se registra solo
                # (0064-0070) deja la suya y el runner no agrega otra; uno que deja
                # MÁS de una en la misma corrida se deshace: sería un duplicado que
                # ya no se puede borrar.
                if despues - (antes or 0) > 1:
                    raise ValueError(f"{m.ruta.name} insertó {despues - (antes or 0)} filas con su nombre en "
                                     "ops.migraciones en una sola corrida: el registro quedaría duplicado. "
                                     "Se deshace entero; deja un solo insert (con `where not exists`).")
                for previa, texto_previo in sin_fila:
                    registrar(cur, previa, "registro_tardio", texto_previo, registrada_con=m.nombre)
                if despues <= (antes or 0):
                    registrar(cur, m, "ejecutada", crudo)
                else:
                    eco(f"  {m.nombre}: la fila la puso el propio archivo (se registra solo); el runner no agrega otra.")
            pg.commit()
        except Exception as exc:  # noqa: BLE001
            pg.rollback()
            raise FalloMigracion(m, exc, hechas, sin_registro=not hay_registro) from exc
        if reg_despues is not None:
            hay_registro = True
            if sin_fila:
                eco(f"  registradas tarde ({len(sin_fila)}): de {sin_fila[0][0].nombre} a {sin_fila[-1][0].nombre}")
                sin_fila = []
        hechas.append(m.nombre)
    if sin_fila:
        eco(f"AVISO: {len(sin_fila)} archivo(s) corrieron sin registro: ops.migraciones no existe todavía.")
    return hechas


def adoptar(pg, plan: Plan, eco=print) -> int:
    """Registra plan.adoptar SIN ejecutar nada, todo en UNA transacción."""
    cur = pg.cursor()
    try:
        cur.execute("select pg_advisory_xact_lock(%s)", (CANDADO,))
        registro = leer_registro(cur) or {}
        n = 0
        for m in plan.adoptar:
            if registro.get(m.nombre, 0) > 0:
                continue
            registrar(cur, m, "adoptada", m.ruta.read_text(encoding="utf-8"),
                      hasta=f"{plan.adoptar_hasta:04d}",
                      nota="registrada sin ejecutar: ya estaba aplicada en esta base",
                      **({"sin_huella_ok": True} if m.nombre in plan.sin_huella_ok else {}))
            n += 1
        pg.commit()
    except Exception:
        pg.rollback()
        raise
    eco(f"Adoptadas {n} migraciones (<= {plan.adoptar_hasta:04d}), sin ejecutar ninguna.")
    return n


# ═══════════════════════════════════════════════════════════════════════════
# Lo que se imprime
# ═══════════════════════════════════════════════════════════════════════════
def _compacto(ms: list[Migracion]) -> str:
    return ", ".join(m.nombre for m in ms) if ms else "—"


def _se_registra_sola(m: Migracion) -> bool:
    """Solo para el plan impreso (el nombre ajeno ya se rechazó antes)."""
    try:
        return m.nombre in registros_propios(m.ruta.read_text(encoding="utf-8"))
    except OSError:
        return False


def imprimir_plan(plan: Plan, eco=print, solo_plan: bool = False) -> None:
    eco("PLAN (no escribe nada):" if solo_plan else "Plan:")
    if plan.recrear:
        eco(f"  tiraría los esquemas {', '.join(ESQUEMAS_PROPIOS)} (drop … cascade)")
    elif plan.base_con_tablas:
        eco("  ops.migraciones no existe y la base YA tiene tablas: no es una base nueva.")
    elif not plan.registro_existe:
        eco("  ops.migraciones no existe: base nueva, corre todo y registra desde que nazca (0064).")
    else:
        eco(f"  ops.migraciones: frontera {plan.frontera:04d} · {len(plan.saltar)} con fila"
            if plan.frontera is not None else "  ops.migraciones: vacía")
    if plan.saltar:
        eco(f"  SALTA ({len(plan.saltar)}): {_compacto(plan.saltar)}")
    if plan.adoptar_hasta is not None:
        eco(f"  ADOPTA sin ejecutar ({len(plan.adoptar)}, <= {plan.adoptar_hasta:04d}): {_compacto(plan.adoptar)}")
        for m in plan.excluidas:
            eco(f"  NO ADOPTA (--excepto): {m.nombre}")
        for n, falta in plan.sin_huella.items():
            marca = "adopta igual (--sin-huella-ok)" if n in plan.sin_huella_ok else "SIN HUELLA"
            eco(f"  {marca}: {n} — {', '.join(falta)}")
        if plan.reemplazadas:
            eco(f"  sin efecto propio al final de la cadena (lo reemplazó una posterior; adoptarlas no "
                f"cambia nada): {', '.join(plan.reemplazadas)}")
        if plan.adoptar:
            eco("  (la huella no ve cuerpos de vistas, grants, comments ni datos)")
    for m in plan.correr:
        etiqueta = "ATRASADA" if m in plan.atrasadas else "CORRE"
        eco(f"  {etiqueta:8} {m.nombre}{'  (se registra sola)' if _se_registra_sola(m) else ''}")
    for m in plan.pospuestas:
        eco(f"  NO CORRE (--hasta {plan.hasta:04d}): {m.nombre} — sigue pendiente")
    if not plan.correr and plan.adoptar_hasta is None and not plan.bloqueo:
        eco("  Nada que correr.")
    for n in plan.huerfanas:
        eco(f"  (en el registro sin archivo en main: {n})")
    if plan.bloqueo:
        eco(f"BLOQUEADO: {plan.bloqueo}")


def paridad(pg) -> int:
    """La paridad de siempre contra el manifiesto. Devuelve 0 / 1. Solo lee (en
    una transacción `read only` que termina en ROLLBACK)."""
    cur = pg.cursor()
    cur.execute("set transaction read only")
    man = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    esquemas = list(man["esquemas"].keys())
    cur.execute(
        "select table_schema, table_name, column_name, data_type, is_nullable "
        "from information_schema.columns where table_schema = any(%s) "
        "order by table_schema, table_name, ordinal_position", (esquemas,))
    sandbox: dict = {}
    for sch, tab, col, dt, nul in cur.fetchall():
        sandbox.setdefault(sch, {}).setdefault(tab, []).append(
            f"{col}:{dt}:{'null' if nul == 'YES' else 'notnull'}")
    pg.rollback()

    faltantes, extras, columnas_dif = [], [], []
    for sch, tablas in man["esquemas"].items():
        for tab, det in tablas.items():
            cols_prod = det["columnas"]
            cols_sand = sandbox.get(sch, {}).get(tab)
            if cols_sand is None:
                faltantes.append(f"{sch}.{tab}")
            elif cols_sand != cols_prod:
                columnas_dif.append({
                    "tabla": f"{sch}.{tab}",
                    "solo_en_prod": sorted(set(cols_prod) - set(cols_sand)),
                    "solo_en_sandbox": sorted(set(cols_sand) - set(cols_prod)),
                })
    for sch, tablas in sandbox.items():
        for tab in tablas:
            if tab not in man["esquemas"].get(sch, {}):
                extras.append(f"{sch}.{tab}")

    veredicto = "PARIDAD OK" if not (faltantes or columnas_dif) else "CON DIFERENCIAS"
    print(json.dumps({
        "veredicto": veredicto,
        "tablas_manifiesto": sum(len(t) for t in man["esquemas"].values()),
        "tablas_sandbox": sum(len(t) for t in sandbox.values()),
        "faltantes_en_sandbox": faltantes,
        "extras_en_sandbox": extras,
        "columnas_diferentes": columnas_dif,
    }, ensure_ascii=False, indent=1))
    return 0 if veredicto == "PARIDAD OK" else 1


# ═══════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════
def _numero(s: str) -> int:
    if not re.fullmatch(r"[0-9]{1,4}", s or ""):
        raise argparse.ArgumentTypeError("NNNN: el número de la migración, p. ej. 0063")
    return int(s)


def argumentos(argv=None):
    ap = argparse.ArgumentParser(description="Migraciones del SANDBOX con registro en ops.migraciones.")
    ap.add_argument("--verificar-solo", action="store_true", help="solo la paridad contra el manifiesto")
    ap.add_argument("--plan", "--ensayo", dest="plan", action="store_true",
                    help="imprime qué correría, saltaría o adoptaría, SIN escribir")
    ap.add_argument("--adoptar-hasta", type=_numero, metavar="NNNN",
                    help="UNA vez en una base existente: registra como aplicadas, sin ejecutarlas, las <= NNNN sin fila")
    ap.add_argument("--excepto", metavar="A,B", help="con --adoptar-hasta: no adoptar estas (se corren después)")
    ap.add_argument("--sin-huella-ok", metavar="A,B",
                    help="con --adoptar-hasta: adoptar estas aunque falte su huella en la base")
    ap.add_argument("--incluir-atrasadas", action="store_true",
                    help="corre también las pendientes por debajo de la frontera")
    ap.add_argument("--hasta", type=_numero, metavar="NNNN",
                    help="corre solo las pendientes <= NNNN; las de arriba siguen pendientes")
    ap.add_argument("--recrear", action="store_true",
                    help="DROP SCHEMA ... CASCADE de los esquemas propios antes de aplicar "
                         "(el simulacro de sandbox desechable; jamás corre contra prod "
                         "por el candado de ref)")
    ap.add_argument("--env", metavar="RUTA", help="env.staging a leer (solo SUPABASE_DB_URL y SUPABASE_PROD_REF)")
    args = ap.parse_args(argv)
    if args.verificar_solo and (args.plan or args.recrear or args.adoptar_hasta is not None or args.incluir_atrasadas
                                or args.hasta is not None):
        ap.error("--verificar-solo va solo")
    if args.hasta is not None and (args.recrear or args.adoptar_hasta is not None):
        ap.error("--hasta solo va al correr lo pendiente: no con --recrear ni con --adoptar-hasta")
    if (args.excepto or args.sin_huella_ok) and args.adoptar_hasta is None:
        ap.error("--excepto y --sin-huella-ok solo van con --adoptar-hasta")
    if args.adoptar_hasta is not None and (args.recrear or args.incluir_atrasadas):
        ap.error("--adoptar-hasta solo adopta: no va con --recrear ni con --incluir-atrasadas")
    return args


def main(argv=None) -> int:
    for flujo in (sys.stdout, sys.stderr):   # una consola cp1252 no truena por un carácter
        if hasattr(flujo, "reconfigure"):
            flujo.reconfigure(errors="replace")
    args = argumentos(argv)
    socket.setdefaulttimeout(60)
    _watchdog()

    env = cargar_env(ruta_env(args.env))
    ref = ref_sandbox(env)
    print(f"Destino sandbox verificado: {ref[:8]}…", flush=True)

    migraciones = listar_migraciones()
    if not migraciones:
        sys.exit(f"ABORT: no hay migraciones en {MIGRACIONES}")

    if args.verificar_solo:
        return paridad(conectar(env["SUPABASE_DB_URL"]))

    try:
        # Todo lo que se lee para decidir va en UNA transacción read only que
        # termina en ROLLBACK (nunca se marca la sesión: el pooler se comparte).
        pg = conectar(env["SUPABASE_DB_URL"])
        cur = pg.cursor()
        cur.execute("set transaction read only")
        if args.recrear:
            plan = armar_plan(migraciones, None, recrear=True)
            # ANTES de tirar nada: lo que la cadena toca sin crear tiene que existir.
            faltan = faltan_requisitos(cur, requisitos_externos(migraciones))
            if faltan:
                plan.bloqueo = (
                    f"--recrear tronaría a la mitad: la cadena toca objetos que no crea y que esta base no tiene: "
                    f"{', '.join(faltan)}. No se tiró nada. Para recrear esta base hace falta antes una migración "
                    "de compatibilidad que los cree (ordenada antes de la primera que los pide, con su acta).")
        else:
            registro = leer_registro(cur)
            plan = armar_plan(migraciones, registro, adoptar_hasta=args.adoptar_hasta,
                              excepto=_nombres(args.excepto),
                              incluir_atrasadas=args.incluir_atrasadas, hasta=args.hasta,
                              base_con_tablas=registro is None and base_con_tablas(cur))
            if plan.adoptar:
                esperadas = huellas_esperadas(migraciones)
                nombres = [m.nombre for m in plan.adoptar]
                cat = leer_catalogo(cur, esquemas_de(esperadas))
                aplicar_huellas(plan, faltantes_en_base(esperadas, cat, nombres), _nombres(args.sin_huella_ok),
                                reemplazadas(esperadas, nombres))
        pg.rollback()
        for m in plan.correr:   # la forma de cada archivo y su fila propia, ANTES de tocar nada
            texto = m.ruta.read_text(encoding="utf-8")
            en_una_transaccion(texto, m.ruta.name)
            revisar_registro_propio(m, texto)
    except ValueError as exc:
        sys.exit(f"ABORT: {exc}")

    imprimir_plan(plan, solo_plan=args.plan)
    if plan.bloqueo:
        return 1
    if args.plan:
        return 0

    if args.recrear:
        print("RECREAR: tirando esquemas propios (drop cascade)…", flush=True)
        cur = pg.cursor()
        for sch in ESQUEMAS_PROPIOS:
            cur.execute(f"drop schema if exists {sch} cascade")
        pg.commit()

    if args.adoptar_hasta is not None:
        adoptar(pg, plan)
    else:
        try:
            hechas = ejecutar(pg, plan)
        except FalloMigracion as f:
            print(f"Confirmadas antes del fallo ({len(f.hechas)}): {', '.join(f.hechas) or '—'}", flush=True)
            if f.sin_registro:
                sys.exit(f"{f} — se deshizo ese archivo entero. OJO: todavía no existía ops.migraciones, así que "
                         "lo confirmado antes NO tiene fila y la base quedó A MEDIAS. La próxima corrida sin "
                         "--recrear se va a detener (base con tablas y sin registro): vuelve a correr con --recrear.")
            sys.exit(f"{f} — se deshizo ese archivo entero (sin fila); la próxima corrida empieza en él.")
        print(f"Migraciones aplicadas: {len(hechas)}.", flush=True)

    print("Lo anterior ya quedó confirmado. Paridad contra el manifiesto (solo lectura):", flush=True)
    return paridad(pg)


if __name__ == "__main__":
    sys.exit(main())
