#!/usr/bin/env python
"""
verificar_rls.py — La red que evita que se vuelva a olvidar el candado.

POR QUÉ EXISTE
--------------
Auditoría del 2026-08-19: desde la 0020, cinco migraciones crearon ocho tablas y
NINGUNA activó su RLS (`0020_enrich_margenes`, `0021_ops_stock_watch_photo`,
`0022_candados_fanout`, `0023_ops_fba_snapshot`, `0027_ops_tiktok_tokens` — ésta
última guarda tokens de OAuth de TikTok, y fue la que el equipo corrigió en su
origen al ver la auditoría). La `0025_blindaje_rls` cerró el resto y
dejó un barrido idempotente, pero un barrido es paliativo: mientras las
migraciones nuevas sigan naciendo sin candado, el hueco se reabre solo.

Esto lo cierra en el único lugar donde sirve — el momento en que se escribe la
migración, no seis meses después en una auditoría.

QUÉ NO ES
---------
No es que la fuga sea explotable hoy. NO lo es: PostgREST expone solo
`public, graphql_public`, y está probado en vivo (los seis esquemas de negocio
responden PGRST106). Pero eso es una casilla del dashboard, no el esquema. La
regla que defendemos es: **si la casilla se mueve, no pasa nada**.

CÓMO FUNCIONA (modo estático, el que corre en CI)
-------------------------------------------------
No necesita base de datos ni credenciales — por eso puede correr en cualquier PR.

Lee `supabase/migrations/*.sql` en el MISMO orden que el runner de la casa
(`sorted(glob)`, que respeta la numeración repetida: dos 0018, tres 0023, dos
0024) y las interpreta como una secuencia de eventos sobre un modelo del esquema:

    create table X             → X existe, sin RLS
    alter table X enable  RLS  → X protegida
    alter table X disable RLS  → X desprotegida
    drop  table X              → X deja de existir
    alter table X set schema Y → X se muda
    alter schema A rename to B → se mudan TODAS las tablas de A

Ese último caso no es teórico: la `0014_retiro_propuestas` renombra el esquema
entero, y lo hace dentro de un `execute '...'`, así que hay que leer el texto
crudo y no solo las sentencias sueltas.

Al final, cualquier tabla que siga existiendo en un esquema de negocio y sin RLS
es una violación, y se reporta CON LA MIGRACIÓN QUE LA CREÓ para que el arreglo
sea obvio.

Las vistas se revisan igual, contra `security_invoker` — con una regla que
costó el cuarto hueco aprender: **`create or replace view` RESETEA las
reloptions**. Medido el 1-sep-2026 en el Postgres de Supabase; una versión
anterior de este script afirmaba lo contrario, y por eso la 0042 despojó a
`market_publicaciones_v` sin que nada sonara (curada en la 0045, hallazgo de la
sesión de Competencia). Todo replace deja la vista DESPROTEGIDA hasta que un
`alter view … set (security_invoker = on)` — o el `with (security_invoker=on)`
inline del propio create — la vuelva a blindar en alguna migración.

LÍMITE CONOCIDO, DICHO DE FRENTE
--------------------------------
El SQL dinámico es invisible para este análisis. El barrido de la 0025 activa RLS
con `execute format('alter table %I.%I ...')` y esta prueba no lo ve — a
propósito: el barrido es la red de producción, ésta es la red del código. Una
tabla creada por SQL dinámico tampoco se detectaría; ninguna migración lo hace
hoy, y si alguna lo hiciera, el modo `--vivo` la caza.

USO
---
    python backend/scripts/verificar_rls.py            # estático (CI). Sin secretos.
    python backend/scripts/verificar_rls.py --vivo     # contra la BD real
                                                       # (usa SUPABASE_DB_URL)

Sale con 0 si todo está en orden, 1 si hay violaciones, 2 si no pudo revisar.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"

# Esquemas donde vive el negocio. `public` entra porque es el ÚNICO expuesto por
# PostgREST: una tabla sin RLS ahí sí es alcanzable de verdad.
ESQUEMAS_NEGOCIO = frozenset({
    "core", "channel", "costing", "enrich", "ops",
    "analytics", "migration", "public", "propuestas_retirado",
})

# ─── Patrones ──────────────────────────────────────────────────────────────
# `[a-z_][a-z0-9_]*` y no `\w+`: los identificadores de Postgres sin comillas se
# pliegan a minúsculas, y ninguno nuestro lleva mayúsculas ni comillas.
ID = r"[a-z_][a-z0-9_]*"
_CREAR_TABLA = re.compile(
    rf"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?({ID})\.({ID})", re.I)
_CREAR_VISTA = re.compile(
    rf"\bcreate\s+(or\s+replace\s+)?(?:materialized\s+)?view\s+"
    rf"(?:if\s+not\s+exists\s+)?({ID})\.({ID})(?:\s+with\s*\(([^)]*)\))?", re.I)
_TIRAR_TABLA = re.compile(
    rf"\bdrop\s+table\s+(?:if\s+exists\s+)?({ID})\.({ID})", re.I)
_TIRAR_VISTA = re.compile(
    rf"\bdrop\s+(?:materialized\s+)?view\s+(?:if\s+exists\s+)?({ID})\.({ID})", re.I)
_RLS = re.compile(
    rf"\balter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?({ID})\.({ID})\s+"
    rf"(enable|disable)\s+row\s+level\s+security", re.I)
_INVOKER = re.compile(
    rf"\balter\s+view\s+(?:if\s+exists\s+)?({ID})\.({ID})\s+set\s*\(\s*"
    rf"security_invoker\s*=\s*(on|true|off|false)\s*\)", re.I)
_MUDAR_OBJ = re.compile(
    rf"\balter\s+(?:table|view)\s+({ID})\.({ID})\s+set\s+schema\s+({ID})", re.I)
# `rename to` a secas — NO `rename column X to Y`, que es otra cosa. La 0017 usa
# esto: crea `market_terms_json` como nombre de paso, la llena y la renombra a
# `market_terms`, que es la que recibe el candado. Sin esta regla, el nombre de
# paso parece una tabla desprotegida que quedó suelta.
_RENOMBRAR_OBJ = re.compile(
    rf"\balter\s+(?:table|view)\s+(?:if\s+exists\s+)?({ID})\.({ID})\s+"
    rf"rename\s+to\s+({ID})", re.I)
_RENOMBRAR_ESQ = re.compile(
    rf"\balter\s+schema\s+({ID})\s+rename\s+to\s+({ID})", re.I)
_TIRAR_ESQ = re.compile(rf"\bdrop\s+schema\s+(?:if\s+exists\s+)?({ID})", re.I)


def _sin_comentarios(sql: str) -> str:
    """Quita `-- …` y `/* … */`.

    Sin esto, el propio encabezado de 0025 —que ENUMERA las tablas que quedaron
    sin RLS -- se leería como si fueran sentencias, y la prueba se acusaría a sí
    misma. Es el falso positivo más fácil de provocar en este repo.
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


class Esquema:
    """Modelo del esquema, reconstruido evento por evento."""

    def __init__(self) -> None:
        # (esquema, nombre) → {"protegido": bool, "origen": str}
        self.tablas: dict[tuple[str, str], dict] = {}
        self.vistas: dict[tuple[str, str], dict] = {}

    # -- helpers -----------------------------------------------------------
    def _mudar_esquema(self, viejo: str, nuevo: str) -> None:
        for coleccion in (self.tablas, self.vistas):
            for (sch, nom) in [k for k in coleccion if k[0] == viejo]:
                coleccion[(nuevo, nom)] = coleccion.pop((sch, nom))

    def _tirar_esquema(self, sch: str) -> None:
        for coleccion in (self.tablas, self.vistas):
            for k in [k for k in coleccion if k[0] == sch]:
                del coleccion[k]

    # -- reproducción ------------------------------------------------------
    def aplicar(self, texto: str, migracion: str) -> None:
        """Reproduce una migración. El orden DENTRO del archivo importa:
        `drop` + `create` de la misma tabla es un patrón real aquí."""
        sql = _sin_comentarios(texto)

        eventos: list[tuple[int, str, tuple]] = []
        for m in _CREAR_TABLA.finditer(sql):
            eventos.append((m.start(), "crear_tabla", m.groups()))
        for m in _CREAR_VISTA.finditer(sql):
            eventos.append((m.start(), "crear_vista", m.groups()))
        for m in _TIRAR_TABLA.finditer(sql):
            eventos.append((m.start(), "tirar_tabla", m.groups()))
        for m in _TIRAR_VISTA.finditer(sql):
            eventos.append((m.start(), "tirar_vista", m.groups()))
        for m in _RLS.finditer(sql):
            eventos.append((m.start(), "rls", m.groups()))
        for m in _INVOKER.finditer(sql):
            eventos.append((m.start(), "invoker", m.groups()))
        for m in _MUDAR_OBJ.finditer(sql):
            eventos.append((m.start(), "mudar", m.groups()))
        for m in _RENOMBRAR_OBJ.finditer(sql):
            eventos.append((m.start(), "renombrar_obj", m.groups()))
        for m in _RENOMBRAR_ESQ.finditer(sql):
            eventos.append((m.start(), "renombrar_esq", m.groups()))
        for m in _TIRAR_ESQ.finditer(sql):
            eventos.append((m.start(), "tirar_esq", m.groups()))

        for _, tipo, g in sorted(eventos, key=lambda e: e[0]):
            if tipo == "crear_tabla":
                sch, nom = g[0].lower(), g[1].lower()
                # `if not exists` sobre algo ya creado no lo desprotege.
                self.tablas.setdefault(
                    (sch, nom), {"protegido": False, "origen": migracion})
            elif tipo == "crear_vista":
                reemplaza = bool(g[0])
                sch, nom = g[1].lower(), g[2].lower()
                opts = (g[3] or "").lower()
                inline = bool(re.search(
                    r"security_invoker\s*=\s*(on|true)", opts))
                # LA TRAMPA SILENCIOSA (0042 → 0045): `create or replace view`
                # RESETEA las reloptions — medido el 1-sep-2026 en el Postgres
                # de Supabase (una vista con security_invoker=on quedó sin
                # opciones tras el replace, dentro de una transacción
                # revertida). El comentario que vivía aquí afirmaba lo
                # contrario, y ese error de doctrina dejó pasar el despojo de
                # market_publicaciones_v sin que el workflow sonara. Un replace
                # deja la vista DESPROTEGIDA salvo que traiga el
                # `with (security_invoker = on)` inline; la cura posterior con
                # `alter view … set (…)` la vuelve a blindar, como siempre.
                if reemplaza or (sch, nom) not in self.vistas:
                    self.vistas[(sch, nom)] = {
                        "protegido": inline, "origen": migracion}
            elif tipo == "tirar_tabla":
                self.tablas.pop((g[0].lower(), g[1].lower()), None)
            elif tipo == "tirar_vista":
                self.vistas.pop((g[0].lower(), g[1].lower()), None)
            elif tipo == "rls":
                t = self.tablas.get((g[0].lower(), g[1].lower()))
                if t is not None:
                    t["protegido"] = g[2].lower() == "enable"
            elif tipo == "invoker":
                v = self.vistas.get((g[0].lower(), g[1].lower()))
                if v is not None:
                    v["protegido"] = g[2].lower() in ("on", "true")
            elif tipo == "mudar":
                sch, nom, destino = (x.lower() for x in g)
                for coleccion in (self.tablas, self.vistas):
                    if (sch, nom) in coleccion:
                        coleccion[(destino, nom)] = coleccion.pop((sch, nom))
            elif tipo == "renombrar_obj":
                sch, viejo, nuevo = (x.lower() for x in g)
                # `rename to` conserva el esquema; solo cambia el nombre.
                for coleccion in (self.tablas, self.vistas):
                    if (sch, viejo) in coleccion:
                        d = coleccion.pop((sch, viejo))
                        # Si el destino ya existía, el rename habría fallado en
                        # Postgres; que gane el que llega es lo más fiel.
                        coleccion[(sch, nuevo)] = d
            elif tipo == "renombrar_esq":
                self._mudar_esquema(g[0].lower(), g[1].lower())
            elif tipo == "tirar_esq":
                self._tirar_esquema(g[0].lower())


def revisar_estatico() -> int:
    archivos = sorted(MIGRACIONES.glob("*.sql"))
    if not archivos:
        print(f"ABORTO: no hay migraciones en {MIGRACIONES}", file=sys.stderr)
        return 2

    esquema = Esquema()
    for f in archivos:
        esquema.aplicar(f.read_text(encoding="utf-8"), f.name)

    tablas_malas = sorted(
        (f"{s}.{n}", d["origen"])
        for (s, n), d in esquema.tablas.items()
        if s in ESQUEMAS_NEGOCIO and not d["protegido"])
    vistas_malas = sorted(
        (f"{s}.{n}", d["origen"])
        for (s, n), d in esquema.vistas.items()
        if s in ESQUEMAS_NEGOCIO and not d["protegido"])

    print(f"Revisadas {len(archivos)} migraciones · "
          f"{len(esquema.tablas)} tablas · {len(esquema.vistas)} vistas")

    if not tablas_malas and not vistas_malas:
        print("OK · toda tabla de negocio nace con RLS y toda vista con "
              "security_invoker.")
        return 0

    print("\nFALLA · se rompió la regla de blindaje.\n", file=sys.stderr)
    if tablas_malas:
        print("Tablas sin `enable row level security`:", file=sys.stderr)
        for obj, origen in tablas_malas:
            print(f"  {obj:52} <- creada en {origen}", file=sys.stderr)
        print("\n  Arreglo: en la MISMA migración que la crea, agrega\n"
              "      alter table <esquema>.<tabla> enable row level security;\n"
              "      grant all on <esquema>.<tabla> to service_role;\n"
              "  Sin políticas: RLS activa + 0 políticas = solo pasa quien hace\n"
              "  bypass (service_role y postgres). Es el patrón desde la 0001.",
              file=sys.stderr)
    if vistas_malas:
        print("\nVistas sin `security_invoker = on`:", file=sys.stderr)
        for obj, origen in vistas_malas:
            print(f"  {obj:52} <- creada en {origen}", file=sys.stderr)
        print("\n  Arreglo: alter view <esquema>.<vista> set (security_invoker = on);\n"
              "  Si no, la vista atiende con el gafete de su dueño (postgres,\n"
              "  que tiene BYPASSRLS) y le entrega todo a quien sea que pregunte.",
              file=sys.stderr)
    print("\n  Contexto completo: docs/PLAN_SEGURIDAD_BD.md", file=sys.stderr)
    return 1


def revisar_vivo() -> int:
    """Contra la base real. Necesita SUPABASE_DB_URL; no corre en CI."""
    try:
        import psycopg2
    except ImportError:
        print("ABORTO: --vivo necesita psycopg2.", file=sys.stderr)
        return 2

    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url:
        print("ABORTO: falta SUPABASE_DB_URL.", file=sys.stderr)
        return 2

    # El pooler en modo transaction (6543) es hostil a las sesiones largas de
    # inspección — y una sesión read-only ahí se pega a una conexión COMPARTIDA.
    # El modo session (5432) es el correcto para esto.
    url = url.replace(":6543/", ":5432/")
    esq = "'" + "','".join(sorted(ESQUEMAS_NEGOCIO)) + "'"

    cn = psycopg2.connect(url, connect_timeout=30)
    cn.autocommit = True
    cur = cn.cursor()
    fallas = 0
    for etiqueta, consulta, arreglo in [
        ("tablas sin RLS",
         f"""select n.nspname||'.'||c.relname
             from pg_class c join pg_namespace n on n.oid = c.relnamespace
             where c.relkind = 'r' and not c.relrowsecurity
               and n.nspname in ({esq}) order by 1""",
         "alter table … enable row level security"),
        ("grants a anon/authenticated",
         """select table_schema||'.'||table_name||' → '||grantee
            from information_schema.role_table_grants
            where grantee in ('anon','authenticated')
              and table_schema not in ('storage','realtime','extensions')
            order by 1""",
         "revoke all on … from anon, authenticated"),
        ("vistas sin security_invoker",
         f"""select n.nspname||'.'||c.relname
             from pg_class c join pg_namespace n on n.oid = c.relnamespace
             where c.relkind = 'v' and n.nspname in ({esq})
               and coalesce(array_to_string(c.reloptions, ','), '')
                   not like '%security_invoker=on%' order by 1""",
         "alter view … set (security_invoker = on)"),
    ]:
        cur.execute(consulta)
        filas = [r[0] for r in cur.fetchall()]
        print(f"  {etiqueta}: {len(filas)}")
        for x in filas[:25]:
            print(f"     - {x}")
        if filas:
            print(f"     arreglo: {arreglo}")
            fallas += len(filas)
    cur.close()
    cn.close()

    if fallas:
        print(f"\nFALLA · {fallas} desviaciones en la base.", file=sys.stderr)
        return 1
    print("\nOK · la base coincide con la regla de blindaje.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--vivo", action="store_true",
                    help="revisa la BD real (SUPABASE_DB_URL) en vez de las migraciones")
    args = ap.parse_args()
    return revisar_vivo() if args.vivo else revisar_estatico()


if __name__ == "__main__":
    sys.exit(main())
