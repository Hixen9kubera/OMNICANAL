#!/usr/bin/env python
"""
verificar_0064_0065.py — Prueba la 0064 (órdenes de venta propias) y la 0065
(inventario de kubera, TEXCO III) en el SANDBOX, sin dejar rastro.

POR QUÉ EXISTE
--------------
Las dos migraciones mueven a la base reglas que antes cuidaba el proceso:
transiciones, inmutabilidad de lo confirmado, libro de solo agregar, saldo =
libro y apartado = Σ reservado (constraint triggers diferidos). Una regla en la
base solo vale si se probó que RECHAZA lo que debe, con el SQLSTATE exacto que
Python va a atrapar, y que DEJA PASAR los flujos del plan (confirmar, entregar,
la puerta del formato, devoluciones, crear_auto).

MODOS
-----
  --en-transaccion   BEGIN; aplica 0064 y 0065 (DOS veces: idempotencia); corre
                     todas las pruebas, cada una en su SAVEPOINT; ROLLBACK.
                     No deja nada. Es el modo para probar antes de aplicar.
  (por omisión)      Corre las pruebas sobre lo YA aplicado: cada prueba en su
                     propia transacción, que termina en ROLLBACK.
  --concurrencia-con-commit
                     Solo en el modo por omisión (migraciones ya aplicadas). Las
                     dos pruebas de concurrencia (dos apartados simultáneos; un
                     renglón nuevo que espera a un confirmar en curso) necesitan
                     DATOS CONFIRMADOS: dos conexiones no ven lo que la otra no ha
                     confirmado. Con esta bandera confirman un SKU de prueba en
                     ENSAYO, corren la carrera y al final cancelan sus OV y
                     corrigen el físico a 0 con su movimiento: ENSAYO queda
                     sin saldo ni apartado del SKU, pero su HISTORIA se queda (el
                     libro y el chat son de solo agregar). Sin ella, las dos se
                     OMITEN. En --en-transaccion no pueden correr: la segunda
                     conexión ni siquiera ve las tablas sin confirmar. Son las
                     únicas pruebas que escriben algo que se queda; piden su
                     propio permiso.

DSN Y CANDADO
-------------
Lee SUPABASE_DB_URL de stdin (una línea) o, si stdin está vacío, de
env.staging (../OMNICANAL/env.staging, o --env RUTA) leyéndolo LÍNEA POR LÍNEA
y quedándose solo con esa: el archivo trae credenciales de producción de MySQL
y Woo que este script nunca carga en memoria. Se NIEGA a correr si el DSN no es
del sandbox (`yvootpbz`) o si menciona producción (`tukwcvsi`). Usa el puerto
5432 (conexión propia, no el pooler compartido). Nunca imprime el DSN ni marca
la sesión como de solo lectura.

USO
---
    grep '^SUPABASE_DB_URL=' ../OMNICANAL/env.staging | cut -d= -f2- \\
      | backend/.venv/Scripts/python.exe backend/scripts/verificar_0064_0065.py --en-transaccion

Sale con 0 si todo pasa, 1 si alguna prueba falla y 2 si no pudo correr.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import traceback
from pathlib import Path

import psycopg2
import psycopg2.extensions

RAIZ = Path(__file__).resolve().parent.parent.parent
MIGRACIONES = RAIZ / "supabase" / "migrations"
ARCHIVOS = ("0064_ops_ordenes_venta.sql", "0065_ops_inventario_kubera.sql")
REF_SANDBOX = "yvootpbz"
REF_PRODUCCION = "tukwcvsi"

TABLAS = ("almacenes", "almacenes_hist", "migraciones", "ov_folio", "ov_ordenes", "ov_lineas",
          "ov_mensajes", "ov_archivos", "stock_almacen", "stock_formato", "stock_formato_linea",
          "stock_formato_evento", "stock_mov", "devoluciones")
SOLO_AGREGAR = ("almacenes_hist", "migraciones", "ov_mensajes", "stock_formato_evento", "stock_mov")
VISTA = "stock_apartado_descuadre_v"
VISTAS = (VISTA, "devoluciones_vs_canal_v")
QUIEN = "verificador"
ENV_POR_OMISION = RAIZ.parent / "OMNICANAL" / "env.staging"


# ═══════════════════════════════════════════════════════════════════════════
# Conexión
# ═══════════════════════════════════════════════════════════════════════════
def leer_dsn(ruta_env: str | None) -> str:
    dsn = ""
    if not sys.stdin.isatty():
        dsn = (sys.stdin.readline() or "").strip()
    if not dsn:
        p = Path(ruta_env) if ruta_env else ENV_POR_OMISION
        if p.exists():
            # En STREAMING y solo la línea SUPABASE_DB_URL: el archivo trae las
            # credenciales de MySQL/Woo de producción y no se cargan nunca. Un `#`
            # dentro del DSN ya viene codificado (%23): no se corta la línea.
            with p.open(encoding="utf-8") as fh:
                dsn = next((ln.split("=", 1)[1].strip() for ln in fh if ln.startswith("SUPABASE_DB_URL=")), "")
    dsn = dsn.strip().strip('"').strip("'")
    if not dsn:
        sys.exit("ABORTO: no hay SUPABASE_DB_URL. Pásala por stdin: "
                 "grep '^SUPABASE_DB_URL=' ../OMNICANAL/env.staging | cut -d= -f2- | python … ")
    if REF_SANDBOX not in dsn or REF_PRODUCCION in dsn:
        sys.exit("ABORTO: el DSN no es el del SANDBOX. Este script solo corre contra el sandbox.")
    return dsn.replace(":6543/", ":5432/")


def conectar(dsn: str):
    try:
        cn = psycopg2.connect(dsn, connect_timeout=20, application_name="verificar_0064_0065")
    except psycopg2.Error as e:  # sin el texto: puede traer el host
        sys.exit(f"ABORTO: no se pudo conectar al sandbox ({type(e).__name__}).")
    cn.autocommit = False
    return cn


CONTROL_TX = re.compile(
    r"(?im)^\s*(?:(?:commit|rollback|abort|savepoint|release)\b|start\s+transaction\b"
    r"|begin\s+(?:transaction|work|isolation)\b|end(?:\s+(?:transaction|work))?\s*;)")


def sql_sin_transaccion(texto: str) -> str:
    """Quita el `begin;` / `commit;` de nivel superior del archivo (también con un
    comentario al final de la línea): dentro de --en-transaccion un `commit` del
    archivo CONFIRMARÍA la transacción de prueba. Cualquier OTRO control de
    transacción a inicio de línea (commit work, end transaction, rollback,
    abort, savepoint, release, start transaction, un `end;` suelto) truena ANTES
    de ejecutar: falla cerrado. Los `end $$;`, `end if;`, `end loop;` y el `end)`
    de un CASE no son control de transacción y pasan."""
    salida = re.sub(r"(?im)^\s*(begin|commit)\s*;\s*(--[^\n]*)?$", "", texto)
    hallado = CONTROL_TX.search(salida)
    if hallado:
        raise RuntimeError(f"control de transacción en la migración: «{hallado.group(0).strip()}»")
    return salida


def leer_migracion(nombre: str) -> str:
    return sql_sin_transaccion((MIGRACIONES / nombre).read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════════════
# Infraestructura de pruebas
# ═══════════════════════════════════════════════════════════════════════════
class Falla(Exception):
    pass


class Omitida(Exception):
    pass


PRUEBAS: list[tuple[str, str, callable]] = []


def prueba(grupo: str, nombre: str):
    def deco(fn):
        PRUEBAS.append((grupo, nombre, fn))
        return fn
    return deco


class Ctx:
    """Lo que recibe cada prueba: el cursor y los ayudantes."""

    def __init__(self, cn, migraciones: dict[str, str], dsn: str, en_transaccion: bool, con_commit: bool):
        self.cn = cn
        self.cur = cn.cursor()
        self.migraciones = migraciones
        self.dsn = dsn
        self.en_transaccion = en_transaccion
        self.con_commit = con_commit
        self._n = 0

    # -- consultas ---------------------------------------------------------
    def q(self, sql, params=None):
        self.cur.execute(sql, params)
        return self.cur.fetchall() if self.cur.description else []

    def uno(self, sql, params=None):
        filas = self.q(sql, params)
        return filas[0] if filas else None

    def valor(self, sql, params=None):
        fila = self.uno(sql, params)
        return fila[0] if fila else None

    def afirma(self, cond, mensaje):
        if not cond:
            raise Falla(mensaje)

    def nuevo(self, prefijo="ZZPRUEBA"):
        self._n += 1
        return f"{prefijo}-{self._n:04d}"

    # -- errores esperados -------------------------------------------------
    def espera(self, sqlstate, sql, params=None, constraint=None, mensaje=None, al_commit=False):
        """Ejecuta `sql` (o una lista) en un SAVEPOINT y exige el SQLSTATE exacto.
        al_commit: además fuerza las revisiones diferidas (SET CONSTRAINTS ALL
        IMMEDIATE) y exige que el error salga AHÍ, no antes."""
        sentencias = sql if isinstance(sql, list) else [(sql, params)]
        self.cur.execute("savepoint espera")
        etapa = "sentencia"
        try:
            for s, p in sentencias:
                self.cur.execute(s, p)
            if al_commit:
                etapa = "commit"
                self.cur.execute("set constraints all immediate")
        except psycopg2.Error as e:
            self.cur.execute("rollback to savepoint espera")
            self.cur.execute("release savepoint espera")
            if e.pgcode != sqlstate:
                raise Falla(f"se esperaba {sqlstate} y salió {e.pgcode}: {(e.diag.message_primary or '')[:160]}")
            nombres = (constraint,) if isinstance(constraint, str) else (constraint or ())
            if nombres and e.diag.constraint_name not in nombres:
                raise Falla(f"{sqlstate} correcto pero constraint={e.diag.constraint_name}, se esperaba {nombres}")
            if mensaje and (e.diag.message_primary or "") != mensaje:
                raise Falla(f"{sqlstate} correcto pero motivo «{e.diag.message_primary}», se esperaba «{mensaje}»")
            if al_commit and etapa != "commit":
                raise Falla(f"{sqlstate} salió en la sentencia y se esperaba al COMMIT (diferido)")
            return f"{e.pgcode}/{e.diag.constraint_name or e.diag.message_primary}"
        self.cur.execute("rollback to savepoint espera")
        self.cur.execute("release savepoint espera")
        raise Falla(f"se esperaba {sqlstate}{'/' + str(constraint) if constraint else ''} y NO hubo error")

    def inmediato(self):
        """Corre ya las revisiones diferidas (lo que pasaría al COMMIT)."""
        self.cur.execute("set constraints all immediate")

    # -- fixtures: las sentencias del plan, con ops.exigir -----------------
    def entrada(self, sku, n, almacen="ENSAYO"):
        self._n += 1
        return self.valor(
            """
            with s as (
              insert into ops.stock_almacen as sa (sku, almacen, fisico)
              values (%(sku)s, %(alm)s, %(n)s)
              on conflict (sku, almacen) do update set fisico = sa.fisico + excluded.fisico
              returning sa.sku, sa.almacen, sa.fisico
            ), m as (
              insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, clave, quien, via)
              select s.sku, s.almacen, %(n)s, s.fisico, 'entrada', 'FMT-PRUEBA', %(clave)s, %(q)s, 'prueba'
                from s
              returning id
            )
            select ops.exigir((select count(*) from m) = 1, 'entrada_no_cuadra')
            """,
            {"sku": sku, "alm": almacen, "n": n, "clave": f"fmt:{900000 + self._n}:{sku}", "q": QUIEN})

    def ov(self, lineas, clave=None, mp=None, tipo="venta", canal=None, creado_via="panel",
           creado_at=None, full_tienda=None, envio_ref=None, cliente="directa"):
        """crear_borrador: folio, encabezado, renglones y mensaje en UNA sentencia.
        lineas: [(sku, cantidad, almacen|None)]. Devuelve (id, folio, rev)."""
        mp = mp or (None, None, None)
        fila = self.uno(
            """
            with f as (
              update ops.ov_folio set ultimo = ultimo + 1 where id = 1 returning ultimo
            ), o as (
              insert into ops.ov_ordenes (folio, estado, tipo, cliente, canal, mp_canal, mp_cuenta, mp_orden,
                                          full_tienda, envio_ref, creado_por, creado_via, clave, creado_at)
              select 'OV-' || lpad(f.ultimo::text, greatest(5, length(f.ultimo::text)), '0'), 'borrador', %(tipo)s,
                     %(cliente)s, %(canal)s, %(mc)s, %(mu)s, %(mo)s, %(ft)s, %(env)s, %(q)s, %(via)s, %(clave)s,
                     coalesce(%(creado)s::timestamptz, now())
                from f
              returning id, folio, rev
            ), l as (
              insert into ops.ov_lineas (orden_id, linea, sku, cantidad, almacen)
              select o.id, t.ord, t.e->>0, (t.e->>1)::int, t.e->>2
                from o, jsonb_array_elements(%(lineas)s::jsonb) with ordinality as t(e, ord)
              returning id
            ), m as (
              insert into ops.ov_mensajes (orden_id, tipo, evento, cuerpo, autor, via)
              select o.id, 'sistema', 'creada', 'Creada (prueba)', %(q)s, 'panel' from o
              returning id
            )
            select o.id, o.folio, o.rev,
                   ops.exigir((select count(*) from l) = jsonb_array_length(%(lineas)s::jsonb), 'renglones_no_cuadran')
              from o
            """,
            {"tipo": tipo, "cliente": cliente, "canal": canal, "mc": mp[0], "mu": mp[1], "mo": mp[2],
             "ft": full_tienda, "env": envio_ref, "q": QUIEN, "via": creado_via, "clave": clave,
             "creado": creado_at, "lineas": json.dumps([list(x) for x in lineas])})
        return fila[0], fila[1], fila[2]

    def lineas(self, orden_id):
        return self.q("select id, sku::text, cantidad, almacen, reservado, entregado from ops.ov_lineas "
                      "where orden_id = %s order by linea", (orden_id,))

    def rev(self, orden_id):
        return self.valor("select rev from ops.ov_ordenes where id = %s", (orden_id,))

    def saldo(self, sku, almacen="ENSAYO"):
        return self.uno("select fisico, apartado, libre from ops.stock_almacen where sku = %s and almacen = %s",
                        (sku, almacen))

    def confirmar(self, orden_id, plan=None, rev=None):
        """La sentencia de confirmar (v2 §4.3 punto 4, admite_ov booleano, ops.exigir)."""
        if plan is None:
            plan = [{"id": l[0], "almacen": l[3] or "ENSAYO"} for l in self.lineas(orden_id)]
        return SQL_CONFIRMAR, {"id": orden_id, "rev": rev if rev is not None else self.rev(orden_id),
                               "plan": json.dumps(plan), "q": QUIEN}

    def entregar(self, orden_id, piezas, rev=None):
        """La sentencia de entregar (v2 §4.3 punto 5). piezas: [(linea_id, n)]."""
        return SQL_ENTREGAR, {"id": orden_id, "rev": rev if rev is not None else self.rev(orden_id),
                              "lineas": json.dumps([{"id": a, "n": b} for a, b in piezas]), "q": QUIEN}

    def cancelar(self, orden_id, motivo="Cancelada en la prueba", origen="manual", rev=None):
        return SQL_CANCELAR, {"id": orden_id, "rev": rev if rev is not None else self.rev(orden_id),
                              "motivo": motivo, "origen": origen, "q": QUIEN}

    def ov_confirmada(self, sku, n, fisico=None):
        """Fixture: entrada de `fisico` en ENSAYO y una OV de `n` piezas confirmada."""
        self.entrada(sku, fisico if fisico is not None else n)
        oid, folio, _ = self.ov([(sku, n, "ENSAYO")])
        s, p = self.confirmar(oid)
        self.q(s, p)
        return oid, folio


# ═══════════════════════════════════════════════════════════════════════════
# Las sentencias del plan (lo que hará el código), con ops.exigir
# ═══════════════════════════════════════════════════════════════════════════
SQL_CONFIRMAR = """
with o as (
  update ops.ov_ordenes v
     set estado = 'confirmada', confirmada_at = now(), confirmada_por = %(q)s, rev = v.rev + 1
   where v.id = %(id)s and v.rev = %(rev)s and v.estado = 'borrador' and v.borrada_at is null
     and (v.tipo <> 'full' or v.envio_ref is not null)
  returning v.id
), plan as materialized (
  select (e->>'id')::bigint as linea_id, e->>'almacen' as almacen
    from jsonb_array_elements(%(plan)s::jsonb) e
), alm as (
  select a.codigo from ops.almacenes a
   where a.codigo in (select almacen from plan) and a.fuente = 'kubera' and a.admite_ov
     for share
), x as materialized (
  select sa.sku, sa.almacen, li.cantidad as n, li.id as linea_id
    from o
    join ops.ov_lineas li on li.orden_id = o.id
    join plan on plan.linea_id = li.id
    join alm on alm.codigo = plan.almacen
    join ops.stock_almacen sa on sa.sku = li.sku and sa.almacen = plan.almacen
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update ops.stock_almacen sa
     set apartado = sa.apartado + x.n
    from x
   where sa.sku = x.sku and sa.almacen = x.almacen and sa.libre >= x.n
  returning sa.sku, sa.almacen
), f as (
  update ops.ov_lineas li set almacen = x.almacen, reservado = li.cantidad
    from x where li.id = x.linea_id
  returning li.id
), msg as (
  insert into ops.ov_mensajes (orden_id, tipo, evento, cuerpo, autor, via)
  select o.id, 'sistema', 'confirmada', 'Confirmada: ' || (select count(*) from x) || ' renglones apartados', %(q)s, 'panel'
    from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'ov_no_esta_en_borrador_o_cambio_rev')
     + ops.exigir((select count(*) from x) > 0
                  and (select count(*) from x) = (select count(*) from ops.ov_lineas where orden_id = %(id)s),
                  'renglon_sin_plan_o_sin_saldo')
     + ops.exigir((select count(*) from s) = (select count(*) from x), 'no_alcanzo')
     + ops.exigir((select count(*) from f) = (select count(*) from x), 'renglones_no_cuadran') as cuadra
"""

SQL_ENTREGAR = """
with p as materialized (
  select (e->>'id')::bigint as linea_id, (e->>'n')::int as n from jsonb_array_elements(%(lineas)s::jsonb) e
), alm as (
  select a.codigo from ops.almacenes a
   where a.fuente = 'kubera'
     and a.codigo in (select li.almacen from ops.ov_lineas li join p on p.linea_id = li.id)
     for share
), o as (
  update ops.ov_ordenes v
     set rev = v.rev + 1,
         estado        = case when t.quedan = 0 then 'entregada' else v.estado end,
         entregada_at  = case when t.quedan = 0 then now() end,
         entregada_por = case when t.quedan = 0 then %(q)s end
    from (select count(*) filter (where li.entregado_at is null
                                    and not exists (select 1 from p where p.linea_id = li.id)) as quedan
            from ops.ov_lineas li where li.orden_id = %(id)s) t
   where v.id = %(id)s and v.rev = %(rev)s and v.estado = 'confirmada' and v.borrada_at is null
     and (v.tipo <> 'full' or v.envio_ref is not null)
  returning v.id, v.folio
), l as materialized (
  select li.id, li.sku, li.almacen, li.cantidad, p.n
    from o join ops.ov_lineas li on li.orden_id = o.id
    join p on p.linea_id = li.id
    join alm on alm.codigo = li.almacen
   where li.entregado_at is null and li.reservado = li.cantidad and p.n between 0 and li.cantidad
), x as materialized (
  select l.id as linea_id, sa.sku, sa.almacen, l.n, l.cantidad
    from l join ops.stock_almacen sa on sa.sku = l.sku and sa.almacen = l.almacen
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update ops.stock_almacen sa
     set fisico = sa.fisico - x.n, apartado = sa.apartado - x.cantidad
    from x where sa.sku = x.sku and sa.almacen = x.almacen
  returning sa.sku, sa.almacen, sa.fisico as saldo, x.n, x.linea_id
), m as (
  insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, ov_linea_id, clave, quien, via)
  select s.sku, s.almacen, -s.n, s.saldo, 'salida_ov', (select folio from o), s.linea_id,
         'ov:' || %(id)s || ':linea:' || s.linea_id || ':salida', %(q)s, 'prueba'
    from s where s.n > 0
  returning ov_linea_id
), r as (
  update ops.ov_lineas li
     set reservado = 0, entregado = s.n, entregado_at = now(), entregado_por = %(q)s
    from s where li.id = s.linea_id
  returning li.id
), msg as (
  insert into ops.ov_mensajes (orden_id, tipo, evento, cuerpo, autor, via)
  select o.id, 'sistema', 'entregada_parcial', 'Entregados ' || (select count(*) from r) || ' renglones', %(q)s, 'panel'
    from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'ov_no_esta_confirmada_o_cambio_rev')
     + ops.exigir((select count(*) from s) = (select count(*) from p)
                  and (select count(*) from r) = (select count(*) from s)
                  and (select count(*) from m) = (select count(*) from s where n > 0), 'entrega_no_cuadra') as cuadra
"""

SQL_CANCELAR = """
with o as (
  update ops.ov_ordenes v
     set estado = 'cancelada', cancelada_at = now(), cancelada_por = %(q)s, cancelada_origen = %(origen)s,
         cancelada_motivo = %(motivo)s, rev = v.rev + 1
   where v.id = %(id)s and v.rev = %(rev)s and v.estado in ('borrador', 'confirmada') and v.borrada_at is null
  returning v.id
), x as materialized (
  select sa.sku, sa.almacen, li.reservado as n, li.id as linea_id
    from o
    join ops.ov_lineas li on li.orden_id = o.id and li.reservado > 0 and li.entregado_at is null
    join ops.stock_almacen sa on sa.sku = li.sku and sa.almacen = li.almacen
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update ops.stock_almacen sa set apartado = sa.apartado - x.n
    from x where sa.sku = x.sku and sa.almacen = x.almacen
  returning sa.sku
), r as (
  update ops.ov_lineas li set reservado = 0 from x where li.id = x.linea_id returning li.id
), msg as (
  insert into ops.ov_mensajes (orden_id, tipo, evento, cuerpo, autor, via)
  select o.id, 'sistema', 'cancelada', 'Cancelada: ' || %(motivo)s, %(q)s, 'panel' from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'ov_no_cancelable_o_cambio_rev')
     + ops.exigir((select count(*) from s) = (select count(*) from x)
                  and (select count(*) from r) = (select count(*) from x), 'cancelar_no_cuadra') as cuadra
"""

SQL_CREAR_AUTO = """
with alm as (             -- C11: surte_ventas se vuelve a comprobar dentro del candado
  select a.codigo from ops.almacenes a
   where a.codigo = %(alm)s and a.fuente = 'kubera' and a.admite_ov and a.surte_ventas
     for share
), previa as (            -- por la clave O por la venta (C1)
  select v.id from ops.ov_ordenes v
   where (v.clave = %(clave)s or (v.mp_canal, v.mp_cuenta, v.mp_orden) = (%(mc)s, %(mu)s, %(mo)s))
     and v.borrada_at is null and v.estado <> 'cancelada'
), r as materialized (
  select (e->>'sku')::citext as sku, (e->>'n')::int as n from jsonb_array_elements(%(lineas)s::jsonb) e
), x as materialized (
  select sa.sku, sa.almacen, r.n
    from ops.stock_almacen sa join alm on alm.codigo = sa.almacen join r on r.sku = sa.sku
   where not exists (select 1 from previa)
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update ops.stock_almacen sa set apartado = sa.apartado + x.n
    from x where sa.sku = x.sku and sa.almacen = x.almacen and sa.libre >= x.n
  returning sa.sku, sa.almacen
), fo as (
  update ops.ov_folio set ultimo = ultimo + 1
   where id = 1 and not exists (select 1 from previa)
     and (select count(*) from s) = (select count(*) from r)
  returning ultimo
), o as (
  insert into ops.ov_ordenes (folio, estado, tipo, cliente, canal, mp_canal, mp_cuenta, mp_orden, creado_por,
                              creado_via, clave, confirmada_at, confirmada_por, precio_origen)
  select 'OV-' || lpad(fo.ultimo::text, greatest(5, length(fo.ultimo::text)), '0'), 'confirmada', 'venta',
         %(mc)s, %(mc)s, %(mc)s, %(mu)s, %(mo)s, 'automatico', 'automatico', %(clave)s, now(), 'automatico', 'marketplace'
    from fo
  returning id
), l as (
  insert into ops.ov_lineas (orden_id, linea, sku, cantidad, almacen, reservado)
  select o.id, row_number() over (order by x.sku), x.sku, x.n, x.almacen, x.n from o, x
  returning id
), m as (
  insert into ops.ov_mensajes (orden_id, tipo, evento, cuerpo, autor, via)
  select o.id, 'sistema', 'creada', 'Creada por la venta (prueba)', 'automatico', 'automatico' from o
  returning id
)
select coalesce((select id from o), (select min(id) from previa)) as id,
       case when exists (select 1 from previa) then 'ya_existia' else 'creada' end as resultado,
       ops.exigir(exists (select 1 from previa)
                  or ((select count(*) from o) = 1 and (select count(*) from l) = (select count(*) from r)),
                  'no_alcanzo') as cuadra
"""

SQL_FORMATO_CARGA = """
with f as (
  insert into ops.stock_formato (almacen, archivo_nombre, archivo_hash, cargado_por)
  values (%(alm)s, 'prueba.xlsx', %(hash)s, %(q)s)
  returning id, folio
), l as (
  insert into ops.stock_formato_linea (formato_id, fila, sku, sku_archivo, cantidad, cantidad_archivo, ubicacion,
                                       odoo_tex2_al_cargar)
  select f.id, t.ord, (t.e->>'sku')::citext, t.e->>'sku', (t.e->>'n')::int, (t.e->>'n')::int, t.e->>'ubic',
         (t.e->>'tex2')::numeric
    from f, jsonb_array_elements(%(lineas)s::jsonb) with ordinality as t(e, ord)
  returning id
), ev as (
  insert into ops.stock_formato_evento (formato_id, evento, quien) select f.id, 'cargado', %(q)s from f
  returning id
)
select f.id, f.folio,
       ops.exigir((select count(*) from l) = jsonb_array_length(%(lineas)s::jsonb), 'renglones_no_cuadran')
  from f
"""

SQL_FORMATO_CONFIRMAR = """
with f as (
  update ops.stock_formato
     set estado = 'confirmado', confirmado_at = now(), confirmado_por = %(q)s, confirmo_bodega = %(bodega)s,
         rev = rev + 1
   where id = %(id)s and rev = %(rev)s and estado = 'por_confirmar'
  returning id
), l as (                 -- registro: lo que Odoo mostraba en TEX2 al confirmar; cruce por citext (H06)
  update ops.stock_formato_linea l set odoo_tex2_al_confirmar = t.v::numeric
    from f, jsonb_each_text(%(tex2)s::jsonb) as t(k, v)
   where l.formato_id = f.id and l.sku = t.k::citext
  returning l.id
), ev as (
  insert into ops.stock_formato_evento (formato_id, evento, quien) select f.id, 'confirmado', %(q)s from f
  returning id
)
select ops.exigir((select count(*) from f) = 1, 'formato_no_esta_por_confirmar')
     + ops.exigir((select count(*) from ops.stock_formato_linea x where x.formato_id = %(id)s) > 0,
                  'formato_sin_renglones') as cuadra
"""

# «Dividir» (plan v3 §3): los renglones no listos pasan a un formato NUEVO por
# confirmar, hijo del mismo archivo (dividido_de, mismo hash y bodega).
SQL_FORMATO_DIVIDIR = """
with p as (
  select id, almacen, archivo_nombre, archivo_hash from ops.stock_formato
   where id = %(id)s and estado = 'por_confirmar'
     for update
), h as (
  insert into ops.stock_formato (almacen, archivo_nombre, archivo_hash, dividido_de, cargado_por)
  select p.almacen, p.archivo_nombre, p.archivo_hash, p.id, %(q)s from p
  returning id, folio
), d as (
  delete from ops.stock_formato_linea l using p
   where l.formato_id = p.id and l.sku = any(%(skus)s::citext[]) and l.salida_odoo_at is null
  returning l.fila, l.sku, l.sku_archivo, l.cantidad, l.cantidad_archivo, l.ubicacion, l.nota,
            l.odoo_tex2_al_cargar, l.odoo_total_al_cargar, l.aviso
), i as (
  insert into ops.stock_formato_linea (formato_id, fila, sku, sku_archivo, cantidad, cantidad_archivo, ubicacion, nota,
                                       odoo_tex2_al_cargar, odoo_total_al_cargar, aviso)
  select h.id, d.fila, d.sku, d.sku_archivo, d.cantidad, d.cantidad_archivo, d.ubicacion, d.nota,
         d.odoo_tex2_al_cargar, d.odoo_total_al_cargar, d.aviso
    from h, d
  returning id
), ev as (
  insert into ops.stock_formato_evento (formato_id, evento, despues, quien)
  select p.id, 'dividido', jsonb_build_object('hijo', h.id, 'renglones', (select count(*) from d)), %(q)s from p, h
  union all
  select h.id, 'cargado', jsonb_build_object('dividido_de', p.id), %(q)s from p, h
  returning id
)
select (select id from h), (select folio from h),
       ops.exigir((select count(*) from h) = 1 and (select count(*) from d) = cardinality(%(skus)s::text[])
                  and (select count(*) from i) = (select count(*) from d), 'dividir_no_cuadra') as cuadra
"""

# La puerta, corregida (C2): sin la CTE `x` que nunca corría; los renglones se
# bloquean en orden (lk) y el upsert entra ordenado por SKU.
SQL_PUERTA = """
with p as materialized (
  select * from jsonb_to_recordset(%(listos)s::jsonb) as p(sku citext, via text, ref text, tex2 numeric)
), f as (
  select id, folio, almacen from ops.stock_formato where id = %(id)s and estado = 'confirmado' for share
), alm as (
  select a.codigo from ops.almacenes a join f on f.almacen = a.codigo where a.fuente = 'kubera' for share of a
), lk as materialized (
  select l.id from ops.stock_formato_linea l join f on l.formato_id = f.id join p on p.sku = l.sku
   where l.salida_odoo_at is null
   order by l.id
     for update of l
), ln as (
  update ops.stock_formato_linea l
     set salida_odoo_at = now(), salida_odoo_via = p.via, salida_odoo_ref = p.ref, odoo_tex2_al_abrir = p.tex2
    from lk, p
   where l.id = lk.id and l.sku = p.sku
  returning l.sku, l.cantidad, l.ubicacion
), e as materialized (
  select sku, sum(cantidad)::int as n, string_agg(distinct ubicacion, ', ') as ubic from ln group by sku
), s as (
  insert into ops.stock_almacen as sa (sku, almacen, fisico, ubicacion)
  select e.sku, alm.codigo, e.n, e.ubic from e cross join alm order by e.sku
  on conflict (sku, almacen) do update
     set fisico = sa.fisico + excluded.fisico, ubicacion = coalesce(excluded.ubicacion, sa.ubicacion)
  returning sa.sku, sa.fisico
), m as (
  insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, clave, quien, via)
  select s.sku, f.almacen, e.n, s.fisico, 'entrada', f.folio, 'fmt:' || f.id || ':' || s.sku, %(q)s, 'prueba'
    from s join e on e.sku = s.sku cross join f
  returning id
), ev as (
  insert into ops.stock_formato_evento (formato_id, evento, despues, quien)
  select f.id, 'puerta_abierta', jsonb_build_object('skus', (select count(*) from e)), %(q)s
    from f where exists (select 1 from e)
  returning id
)
select (select count(*) from e) as abiertos,
       ops.exigir((select count(*) from f) = 1 and (select count(*) from alm) = 1
                  and (select count(*) from s) = (select count(*) from e)
                  and (select count(*) from m) = (select count(*) from e), 'puerta_no_cuadra') as cuadra
"""

SQL_DEV_RECIBIR = """
with li as (
  select l.id, l.sku, l.entregado, o.id as orden_id
    from ops.ov_lineas l join ops.ov_ordenes o on o.id = l.orden_id
   where l.id = %(linea)s and o.estado in ('entregada', 'entregada_cancelada') and o.borrada_at is null
), alm as (
  select a.codigo from ops.almacenes a where a.codigo = 'REVISION' and a.fuente = 'kubera' for share
), dv as (                 -- la guarda de la recaptura: Σ devuelto + n ≤ entregado
  select coalesce(sum(m.delta), 0) as devueltas from ops.stock_mov m
   where m.motivo = 'devolucion' and m.ov_linea_id = %(linea)s
), d as (
  insert into ops.devoluciones (origen, sku, cantidad, ov_linea_id, paquete_ref, paquete_ids, empaque,
                                recibido_por, clave)
  select 'venta', li.sku, %(n)s, li.id, %(paq)s, %(ids)s, 'cerrado', %(q)s, %(clave)s
    from li, dv where dv.devueltas + %(n)s <= li.entregado
  returning id, folio, sku, cantidad, ov_linea_id
), s as (
  insert into ops.stock_almacen as sa (sku, almacen, fisico)
  select d.sku, alm.codigo, d.cantidad from d, alm
  on conflict (sku, almacen) do update set fisico = sa.fisico + excluded.fisico
  returning sa.sku, sa.almacen, sa.fisico
), m as (
  insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, clave, ov_linea_id, quien, via)
  select s.sku, s.almacen, d.cantidad, s.fisico, 'devolucion', d.folio, 'dev:' || d.id || ':recibe',
         d.ov_linea_id, %(q)s, 'prueba'
    from s, d
  returning id
), o as (
  update ops.ov_ordenes v set devolucion_estado = 'recibida', rev = v.rev + 1
    from li where v.id = li.orden_id
  returning v.id
)
select (select id from d) as devolucion_id,
       ops.exigir((select count(*) from d) = 1, 'devolucion_de_mas_o_sin_venta')
     + ops.exigir((select count(*) from m) = 1 and (select count(*) from o) = 1, 'devolucion_no_cuadra') as cuadra
"""

SQL_DEV_VENDIBLE = """
with d as (
  update ops.devoluciones
     set dictamen = 'vendible', dictamen_at = now(), dictamen_por = %(q)s, resuelta_at = now(), rev = rev + 1
   where id = %(id)s and dictamen is null and resuelta_at is null
  returning id, sku, cantidad
), x as materialized (     -- candados en orden: (sku, ENSAYO) antes que (sku, REVISION)
  select sa.sku, sa.almacen from ops.stock_almacen sa join d on d.sku = sa.sku
   where sa.almacen in ('ENSAYO', 'REVISION')
   order by sa.sku, sa.almacen
     for update of sa
), s1 as (
  update ops.stock_almacen sa set fisico = sa.fisico - d.cantidad
    from d where sa.sku = d.sku and sa.almacen = 'REVISION'
  returning sa.sku, sa.fisico
), s2 as (
  insert into ops.stock_almacen as sa (sku, almacen, fisico)
  select d.sku, 'ENSAYO', d.cantidad from d
  on conflict (sku, almacen) do update set fisico = sa.fisico + excluded.fisico
  returning sa.sku, sa.fisico
), m as (
  insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, clave, quien, via)
  select d.sku, 'REVISION', -d.cantidad, s1.fisico, 'traspaso_salida', 'DEV-' || d.id, 'dev:' || d.id || ':sale', %(q)s, 'prueba'
    from d, s1
  union all
  select d.sku, 'ENSAYO', d.cantidad, s2.fisico, 'traspaso_entrada', 'DEV-' || d.id, 'dev:' || d.id || ':entra', %(q)s, 'prueba'
    from d, s2
  returning id
)
select ops.exigir((select count(*) from d) = 1 and (select count(*) from x) >= 1
                  and (select count(*) from m) = 2, 'dictamen_no_cuadra') as cuadra
"""

# «¿Salió?» tardío (decisión 15 de la 0064, DEVOLUCIONES §4b punto 2): la caja de
# una OV ya cancelada sí salió. Primero se registra la salida, con la clave de
# entregar; después, la devolución normal.
SQL_SALIO_TARDE = """
with o as (
  update ops.ov_ordenes v
     set estado = 'entregada_cancelada', entregada_at = now(), entregada_por = %(q)s,
         devolucion_estado = 'pendiente', rev = v.rev + 1
   where v.id = %(id)s and v.estado = 'cancelada' and v.borrada_at is null
  returning v.id, v.folio
), x as materialized (
  select li.id as linea_id, sa.sku, sa.almacen, li.cantidad
    from o join ops.ov_lineas li on li.orden_id = o.id and li.entregado_at is null
    join ops.stock_almacen sa on sa.sku = li.sku and sa.almacen = li.almacen
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update ops.stock_almacen sa set fisico = sa.fisico - x.cantidad
    from x where sa.sku = x.sku and sa.almacen = x.almacen
  returning sa.sku, sa.almacen, sa.fisico, x.cantidad as n, x.linea_id
), m as (
  insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, ov_linea_id, clave, quien, via)
  select s.sku, s.almacen, -s.n, s.fisico, 'salida_ov', (select folio from o), s.linea_id,
         'ov:' || %(id)s || ':linea:' || s.linea_id || ':salida', %(q)s, 'prueba'
    from s
  returning id
), r as (
  update ops.ov_lineas li set entregado = s.n, entregado_at = now(), entregado_por = %(q)s
    from s where li.id = s.linea_id
  returning li.id
), msg as (
  insert into ops.ov_mensajes (orden_id, tipo, evento, cuerpo, autor, via)
  select o.id, 'sistema', 'devolucion_esperada', 'Bodega: la caja de la OV cancelada sí salió', %(q)s, 'panel' from o
  returning id
)
select ops.exigir((select count(*) from o) = 1 and (select count(*) from x) > 0
                  and (select count(*) from m) = (select count(*) from x)
                  and (select count(*) from r) = (select count(*) from x), 'salio_tarde_no_cuadra') as cuadra
"""

SQL_CONTEO = """
with x as (
  select sa.sku, sa.almacen, sa.fisico from ops.stock_almacen sa
   where sa.sku = %(sku)s and sa.almacen = %(alm)s and sa.fisico = %(visto)s
     for update
), s as (
  update ops.stock_almacen sa set fisico = %(contado)s
    from x where sa.sku = x.sku and sa.almacen = x.almacen
  returning sa.sku, sa.almacen, sa.fisico, x.fisico as antes
), m as (
  insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, clave, nota, quien, via)
  select s.sku, s.almacen, s.fisico - s.antes, s.fisico, 'ajuste_conteo',
         'conteo:S1:' || s.sku || ':' || s.almacen, 'Conteo de prueba', %(q)s, 'prueba'
    from s where s.fisico <> s.antes
  returning id
)
select ops.exigir((select count(*) from s) = 1, 'recontar') as cuadra
"""


# ═══════════════════════════════════════════════════════════════════════════
# 1. FORMA
# ═══════════════════════════════════════════════════════════════════════════
@prueba("forma", "tablas_y_vista")
def _(c: Ctx):
    filas = dict(c.q("select c.relname, c.relkind from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                     "where n.nspname = 'ops' and c.relname = any(%s)", (list(TABLAS) + list(VISTAS),)))
    faltan = [t for t in TABLAS if filas.get(t) != "r"]
    c.afirma(not faltan, f"faltan tablas: {faltan}")
    c.afirma(all(filas.get(v) == "v" for v in VISTAS), f"faltan vistas vigía: {[v for v in VISTAS if filas.get(v) != 'v']}")
    # La vigía principal no depende de channel.* (la reversa de la 0049 no la toca).
    dep = c.valor("""select count(*) from pg_depend d join pg_rewrite r on r.oid = d.objid
                      join pg_class v on v.oid = r.ev_class join pg_class t on t.oid = d.refobjid
                      join pg_namespace n on n.oid = t.relnamespace
                     where v.oid = 'ops.stock_apartado_descuadre_v'::regclass and n.nspname = 'channel'""")
    c.afirma(dep == 0, f"la vigía principal depende de {dep} objetos de channel.*")
    return f"{len(TABLAS)} tablas + {len(VISTAS)} vistas; la vigía principal sin channel.*"


@prueba("forma", "columnas_y_tipos")
def _(c: Ctx):
    esperado = {
        ("almacenes", "admite_ov"): "boolean", ("almacenes", "surte_ventas"): "boolean",
        ("almacenes", "preferencia"): "smallint", ("ov_ordenes", "canal_cancelo_at"): "timestamp with time zone",
        ("ov_ordenes", "canal_cancelo_ref"): "text", ("ov_ordenes", "tipo"): "text",
        ("ov_lineas", "sku"): "citext", ("ov_lineas", "fuente"): "text",
        ("stock_formato_linea", "odoo_tex2_al_cargar"): "numeric(14,3)",
        ("stock_formato_linea", "odoo_tex2_al_abrir"): "numeric(14,3)",
        ("stock_formato_linea", "cantidad_archivo"): "integer", ("stock_formato_linea", "sku_archivo"): "text",
        ("stock_formato", "descartado_por"): "text", ("stock_mov", "saldo_despues"): "integer",
        ("devoluciones", "paquete_ids"): "text[]", ("stock_watch_photo", "stock_kubera"): "integer",
        ("ov_archivos", "tipo"): "text", ("stock_formato", "dividido_de"): "bigint",
        ("stock_formato_linea", "rev"): "integer", ("devoluciones", "partida_de"): "bigint",
    }
    filas = {(t, col): (ty, gen) for t, col, ty, gen in c.q(
        "select c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attgenerated "
        "from pg_attribute a join pg_class c on c.oid = a.attrelid join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'ops' and a.attnum > 0 and not a.attisdropped")}
    malas = [f"{t}.{col}={filas.get((t, col), ('—',))[0]} (esp. {ty})"
             for (t, col), ty in esperado.items() if filas.get((t, col), ("",))[0] != ty]
    c.afirma(not malas, "; ".join(malas))
    for t, col in (("stock_almacen", "libre"), ("stock_formato", "folio"), ("devoluciones", "folio")):
        c.afirma(filas.get((t, col), ("", ""))[1] == "s", f"{t}.{col} no es columna generada STORED")
    c.afirma(("ov_ordenes", "almacen") not in filas, "ov_ordenes.almacen existe (versión de Brandon)")
    return f"{len(esperado)} columnas + 3 generadas; sin ov_ordenes.almacen"


@prueba("forma", "pk_y_fk_sin_cascada")
def _(c: Ctx):
    filas = {n: (tipo, d, borra) for n, tipo, d, borra in c.q(
        "select k.conname, k.contype, pg_get_constraintdef(k.oid), k.confdeltype from pg_constraint k "
        "join pg_class c on c.oid = k.conrelid join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'ops' and c.relname = any(%s) and k.contype in ('p', 'f')", (list(TABLAS),))}
    pks = {f"{t}_pkey" for t in TABLAS}
    faltan = sorted(pks - set(filas))
    c.afirma(not faltan, f"faltan PK: {faltan}")
    c.afirma(filas["stock_almacen_pkey"][1] == "PRIMARY KEY (sku, almacen)", "PK de stock_almacen no es (sku, almacen)")
    fks = {
        "ov_lineas_orden_fk": "REFERENCES ops.ov_ordenes(id)",
        "ov_lineas_almacen_fk": "FOREIGN KEY (almacen, fuente) REFERENCES ops.almacenes(codigo, fuente)",
        "ov_mensajes_orden_fk": "REFERENCES ops.ov_ordenes(id)",
        "ov_archivos_orden_fk": "REFERENCES ops.ov_ordenes(id)",
        "stock_almacen_almacen_fk": "FOREIGN KEY (almacen, fuente) REFERENCES ops.almacenes(codigo, fuente)",
        "stock_formato_almacen_fk": "FOREIGN KEY (almacen, fuente) REFERENCES ops.almacenes(codigo, fuente)",
        "stock_formato_reemplaza_fk": "REFERENCES ops.stock_formato(id)",
        "stock_formato_dividido_fk": "FOREIGN KEY (dividido_de) REFERENCES ops.stock_formato(id)",
        "devoluciones_partida_fk": "FOREIGN KEY (partida_de) REFERENCES ops.devoluciones(id)",
        "stock_formato_linea_formato_fk": "REFERENCES ops.stock_formato(id)",
        "stock_formato_evento_formato_fk": "REFERENCES ops.stock_formato(id)",
        "stock_mov_almacen_fk": "FOREIGN KEY (almacen, fuente) REFERENCES ops.almacenes(codigo, fuente)",
        "stock_mov_ov_linea_fk": "REFERENCES ops.ov_lineas(id)",
        "devoluciones_ov_linea_fk": "REFERENCES ops.ov_lineas(id)",
    }
    malas = [n for n, frag in fks.items() if n not in filas or frag not in filas[n][1]]
    c.afirma(not malas, f"FK faltantes o distintas: {malas}")
    cascadas = [n for n, (tipo, _, borra) in filas.items() if tipo == "f" and borra != "a"]
    c.afirma(not cascadas, f"FK con borrado en cascada/set null: {cascadas}")
    return f"{len(pks)} PK, {len(fks)} FK, todas NO ACTION"


@prueba("forma", "indices_parciales")
def _(c: Ctx):
    defs = dict(c.q("select indexname, indexdef from pg_indexes where schemaname = 'ops'"))
    esperado = {
        "ov_ordenes_mp_uq": ["UNIQUE", "WHERE", "cancelada"],
        "ov_ordenes_clave_uq": ["UNIQUE", "WHERE", "cancelada"],
        "ov_ordenes_salio_ix": ["canal_cancelo_at", "WHERE"],
        "ov_ordenes_estado_ix": ["WHERE"],
        "ov_lineas_apartan_ix": ["(sku, almacen)", "reservado > 0"],
        "ov_lineas_alm_sku_ix": ["entregado_at IS NULL"],
        "ov_lineas_sku_alm_uq": ["UNIQUE", "NULLS NOT DISTINCT"],
        "ov_mensajes_orden_ix": ["(orden_id, id)"],
        "ov_archivos_vivo_uq": ["UNIQUE", "borrado_at IS NULL"],
        "almacenes_hist_codigo_ix": ["(codigo, id)"],
        "stock_formato_hash_uq": ["UNIQUE", "descartado", "dividido_de IS NULL"],
        "stock_formato_reemplaza_ix": ["reemplaza_a IS NOT NULL"],
        "stock_formato_dividido_ix": ["dividido_de IS NOT NULL"],
        "devoluciones_recepcion_uq": ["UNIQUE", "(ov_linea_id, sku, paquete_ids)", "partida_de IS NULL"],
        "devoluciones_retiro_uq": ["UNIQUE", "(canal, cuenta, sku, paquete_ids)", "retiro_full", "partida_de IS NULL"],
        "devoluciones_partida_ix": ["partida_de IS NOT NULL"],
        "stock_formato_linea_espera_ix": ["salida_odoo_at IS NULL"],
        "stock_formato_evento_fmt_ix": ["(formato_id, id)"],
        "stock_mov_sku_alm_id_ix": ["(sku, almacen, id)"],
        "stock_mov_ov_linea_ix": ["ov_linea_id IS NOT NULL"],
        "stock_mov_entradas_ix": ["(creado_at)", "entrada"],
        "stock_mov_salida_ov_uq": ["UNIQUE", "salida_ov"],
        "stock_mov_clave_uq": ["UNIQUE", "(clave)"],
        "devoluciones_abiertas_ix": ["resuelta_at IS NULL"],
        "devoluciones_paquete_ix": ["gin"],
        "devoluciones_ov_linea_ix": ["ov_linea_id IS NOT NULL"],
        "devoluciones_clave_uq": ["UNIQUE"],
    }
    malos = [n for n, frags in esperado.items() if n not in defs or any(f not in defs[n] for f in frags)]
    c.afirma(not malos, f"índices faltantes o distintos: {malos}")
    c.afirma("COALESCE" not in defs["ov_ordenes_mp_uq"], "ov_ordenes_mp_uq lleva coalesce (versión de Brandon)")
    return f"{len(esperado)} índices"


@prueba("forma", "rls_sin_politicas")
def _(c: Ctx):
    filas = dict(c.q("select c.relname, c.relrowsecurity from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                     "where n.nspname = 'ops' and c.relname = any(%s)", (list(TABLAS),)))
    sin = [t for t in TABLAS if not filas.get(t)]
    c.afirma(not sin, f"sin RLS: {sin}")
    pol = c.valor("select count(*) from pg_policies where schemaname = 'ops' and tablename = any(%s)", (list(TABLAS),))
    c.afirma(pol == 0, f"{pol} políticas (el patrón es 0)")
    return "RLS activa y 0 políticas en las 14"


@prueba("forma", "grants")
def _(c: Ctx):
    malos = []
    # En las de solo agregar, NADA fuera de SELECT e INSERT (MAINTAIN existe desde PG17).
    extra = "UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER"
    if (c.valor("select current_setting('server_version_num')::int") or 0) >= 170000:
        extra += ",MAINTAIN"
    for t in TABLAS + VISTAS:
        q = f"ops.{t}"
        if not c.valor("select has_table_privilege('service_role', %s, 'SELECT')", (q,)):
            malos.append(f"service_role sin SELECT en {t}")
        for rol in ("anon", "authenticated"):
            if c.valor("select has_table_privilege(%s, %s, 'SELECT,INSERT,UPDATE,DELETE')", (rol, q)):
                malos.append(f"{rol} con permisos en {t}")
        if t in VISTAS:
            continue
        if not c.valor("select has_table_privilege('service_role', %s, 'INSERT')", (q,)):
            malos.append(f"service_role sin INSERT en {t}")
        if t in SOLO_AGREGAR and c.valor("select has_table_privilege('service_role', %s, %s)", (q, extra)):
            malos.append(f"service_role tiene alguno de {extra} en {t} (solo agregar)")
        if t not in SOLO_AGREGAR and not c.valor("select has_table_privilege('service_role', %s, 'UPDATE')", (q,)):
            malos.append(f"service_role sin UPDATE en {t}")
    c.afirma(not malos, "; ".join(malos))
    return f"service_role: solo SELECT/INSERT en las 5 de solo agregar (sin {extra}); anon/authenticated: nada"


@prueba("forma", "vista_invoker")
def _(c: Ctx):
    sin = []
    for v in VISTAS:
        opts = c.valor("select reloptions from pg_class where oid = %s::regclass", (f"ops.{v}",))
        if not (opts and "security_invoker=on" in opts):
            sin.append(f"{v}: {opts}")
    c.afirma(not sin, f"sin security_invoker: {sin}")
    return f"{len(VISTAS)} vistas con security_invoker=on"


@prueba("forma", "comentarios")
def _(c: Ctx):
    sin = [t for t in TABLAS + VISTAS
           if not c.valor("select obj_description(%s::regclass, 'pg_class')", (f"ops.{t}",))]
    cols = [("stock_mov", "clave"), ("stock_watch_photo", "stock_kubera"), ("ov_ordenes", "canal_cancelo_at"),
            ("stock_formato_linea", "salida_odoo_at"), ("ov_ordenes", "clave"), ("almacenes", "cuenta_para_woo"),
            ("stock_mov", "ov_linea_id"), ("devoluciones", "clave"), ("stock_formato", "dividido_de"),
            ("stock_formato_linea", "rev"), ("devoluciones", "partida_de")]
    for t, col in cols:
        n = c.valor("select a.attnum from pg_attribute a where a.attrelid = %s::regclass and a.attname = %s",
                    (f"ops.{t}", col))
        if not n or not c.valor("select col_description(%s::regclass, %s)", (f"ops.{t}", n)):
            sin.append(f"{t}.{col}")
    c.afirma(not sin, f"sin comment: {sin}")
    return f"{len(TABLAS) + len(VISTAS)} objetos y {len(cols)} columnas con comment"


@prueba("forma", "triggers_y_diferidos")
def _(c: Ctx):
    filas = {n: (t, cons, defer, ini) for n, t, cons, defer, ini in c.q(
        "select tg.tgname, c.relname, tg.tgconstraint <> 0, coalesce(k.condeferrable, false), "
        "coalesce(k.condeferred, false) from pg_trigger tg join pg_class c on c.oid = tg.tgrelid "
        "join pg_namespace n on n.oid = c.relnamespace left join pg_constraint k on k.oid = tg.tgconstraint "
        "where n.nspname = 'ops' and not tg.tgisinternal")}
    normales = ["almacenes_guarda", "almacenes_hist", "almacenes_hist_solo_agregar", "almacenes_hist_sin_truncate",
                "migraciones_solo_agregar", "migraciones_sin_truncate", "ov_ordenes_guarda", "ov_lineas_guarda",
                "ov_mensajes_solo_agregar", "ov_mensajes_sin_truncate", "stock_almacen_guarda", "stock_formato_guarda",
                "stock_formato_linea_guarda", "stock_mov_solo_agregar", "stock_mov_sin_truncate",
                "stock_formato_evento_solo_agregar", "stock_formato_evento_sin_truncate", "devoluciones_guarda",
                "almacenes_sin_truncate", "ov_folio_sin_truncate", "ov_ordenes_sin_truncate", "ov_lineas_sin_truncate",
                "stock_almacen_sin_truncate", "stock_formato_sin_truncate", "stock_formato_linea_sin_truncate",
                "devoluciones_sin_truncate", "ov_archivos_guarda", "ov_archivos_sin_truncate", "ov_folio_guarda"]
    diferidos = ["ov_ordenes_coherente", "ov_lineas_coherente", "stock_mov_cuadra", "stock_almacen_cuadra",
                 "stock_almacen_apartado_cuadra", "ov_lineas_apartado_cuadra", "ov_ordenes_apartado_cuadra",
                 "stock_mov_devolucion_cuadra"]
    faltan = [n for n in normales + diferidos if n not in filas]
    c.afirma(not faltan, f"faltan triggers: {faltan}")
    no_dif = [n for n in diferidos if not (filas[n][1] and filas[n][2] and filas[n][3])]
    c.afirma(not no_dif, f"no son constraint triggers DEFERRABLE INITIALLY DEFERRED: {no_dif}")
    return f"{len(normales)} de fila/sentencia + {len(diferidos)} constraint triggers diferidos"


@prueba("forma", "funciones_y_permisos")
def _(c: Ctx):
    firmas = ["ops.exigir(boolean,text)", "ops.tg_solo_agregar()", "ops.verificar_ov(bigint)",
              "ops.verificar_libro(citext,text)", "ops.verificar_apartado(citext,text)",
              "ops.tg_ov_ordenes_guarda()", "ops.tg_ov_lineas_guarda()", "ops.tg_stock_almacen_guarda()",
              "ops.tg_libro_cuadra()", "ops.tg_apartado_cuadra()", "ops.tg_almacenes_guarda()",
              "ops.tg_sin_truncate()", "ops.tg_almacenes_hist()", "ops.tg_ov_coherente()",
              "ops.tg_stock_formato_guarda()", "ops.tg_stock_formato_linea_guarda()", "ops.tg_devoluciones_guarda()",
              "ops.tg_ov_archivos_guarda()", "ops.tg_ov_folio_guarda()", "ops.tg_devolucion_de_mas()"]
    faltan = [f for f in firmas if not c.valor("select to_regprocedure(%s) is not null", (f,))]
    c.afirma(not faltan, f"faltan funciones: {faltan}")
    sin_path = [f for f in firmas
                if not any(x.startswith("search_path=") for x in (c.valor(
                    "select proconfig from pg_proc where oid = to_regprocedure(%s)", (f,)) or []))]
    c.afirma(not sin_path, f"sin search_path fijo: {sin_path}")
    # Las que comparan sku (citext) necesitan el esquema de citext en su search_path,
    # o `citext = citext` cae en silencio al operador de text.
    esquema_citext = c.valor("select n.nspname from pg_type t join pg_namespace n on n.oid = t.typnamespace "
                             "where t.typname = 'citext'")
    sin_citext = [f for f in ("ops.verificar_libro(citext,text)", "ops.verificar_apartado(citext,text)",
                              "ops.tg_libro_cuadra()", "ops.tg_apartado_cuadra()", "ops.tg_devoluciones_guarda()")
                  if esquema_citext not in next((x for x in (c.valor("select proconfig from pg_proc where oid = "
                                                                      "to_regprocedure(%s)", (f,)) or [])
                                                 if x.startswith("search_path=")), "")]
    c.afirma(not sin_citext, f"sin el esquema de citext ({esquema_citext}) en su search_path: {sin_citext}")
    for f in firmas[:1] + firmas[2:5]:
        c.afirma(not c.valor("select has_function_privilege('anon', %s, 'execute')", (f,)), f"anon puede ejecutar {f}")
        c.afirma(c.valor("select has_function_privilege('service_role', %s, 'execute')", (f,)),
                 f"service_role no puede ejecutar {f}")
    return f"{len(firmas)} funciones con search_path; anon sin EXECUTE"


@prueba("forma", "semilla_almacenes")
def _(c: Ctx):
    filas = c.q("select codigo, nombre, fuente, odoo_warehouse_id, preferencia, surte_ventas, admite_ov, "
                "cuenta_para_woo from ops.almacenes order by codigo")
    esperado = [
        ("DROP", "DROP OFF", "odoo", 142, None, False, False, True),
        ("ENSAYO", "Bodega de ensayo", "kubera", None, None, False, True, False),
        ("REVISION", "Revisión de devoluciones", "kubera", None, None, False, False, False),
        ("TEX2", "TEXCO II", "odoo", 150, 2, True, False, True),
        ("TEX3", "TEXCO III", "kubera", None, 3, False, False, False),
        ("TEXCO", "TEXCO", "odoo", 135, 1, True, False, True),
    ]
    c.afirma([tuple(f) for f in filas] == esperado, f"semilla distinta: {filas}")
    # Recién aplicada: (1, 0). Tras --concurrencia-con-commit quedan OV confirmadas
    # (canceladas), así que la regla es la de fondo: el contador va en el folio
    # más alto que existe, sin huecos ni adelantos.
    folio = c.uno("select f.id, f.ultimo, coalesce((select max(substr(o.folio, 4)::int) from ops.ov_ordenes o), 0) "
                  "from ops.ov_folio f")
    c.afirma(folio[0] == 1 and folio[1] == folio[2], f"ov_folio (id, ultimo, folio más alto) = {folio}")
    altas = c.valor("select count(*) from ops.almacenes_hist where antes is null")
    c.afirma(altas == 6, f"almacenes_hist tiene {altas} altas (esperado 6: la semilla no se repite)")
    return (f"6 bodegas, TEX3 apagada (surte/woo/admite = false), ov_folio en {folio[1]} = folio más alto, "
            "6 altas en la historia")


# ═══════════════════════════════════════════════════════════════════════════
# 2. IDEMPOTENCIA Y PASO 0
# ═══════════════════════════════════════════════════════════════════════════
@prueba("idempotencia", "registro_de_aplicaciones")
def _(c: Ctx):
    filas = dict(c.q("select migracion, count(*) from ops.migraciones group by 1"))
    esperado = 2 if c.en_transaccion else 1
    c.afirma(filas.get("0064_ops_ordenes_venta", 0) >= esperado and filas.get("0065_ops_inventario_kubera", 0) >= esperado,
             f"ops.migraciones: {filas}")
    return f"aplicadas {filas.get('0064_ops_ordenes_venta')}× y {filas.get('0065_ops_inventario_kubera')}× sin error"


@prueba("idempotencia", "semilla_no_pisa")
def _(c: Ctx):
    c.q("update ops.almacenes set nombre = 'TEXCO III (editado)' where codigo = 'TEX3'")
    c.q(c.migraciones["0064"])
    nombre = c.valor("select nombre from ops.almacenes where codigo = 'TEX3'")
    c.afirma(nombre == "TEXCO III (editado)", f"re-correr la 0064 pisó el nombre: {nombre}")
    c.afirma(c.valor("select count(*) from ops.almacenes") == 6, "re-correr la 0064 cambió el número de bodegas")
    return "re-correr la 0064 conserva lo editado"


@prueba("idempotencia", "paso0_columna_vieja")
def _(c: Ctx):
    c.q("alter table ops.ov_ordenes add column almacen text")
    return c.espera("KB000", c.migraciones["0064"])


@prueba("idempotencia", "paso0_check_viejo")
def _(c: Ctx):
    c.q("alter table ops.ov_lineas drop constraint ov_lineas_reservado_chk")
    c.q("alter table ops.ov_lineas add constraint ov_lineas_reservado_chk check (reservado >= 0 and reservado <= cantidad)")
    return c.espera("KB000", c.migraciones["0064"])


@prueba("idempotencia", "paso0_0065_forma_vieja")
def _(c: Ctx):
    c.q("alter table ops.stock_almacen add column otorgado integer")
    return c.espera("KB000", c.migraciones["0065"])


@prueba("idempotencia", "paso0_unico_inmediato")
def _(c: Ctx):
    # Un borrador anterior con la llave de `linea` INMEDIATA: el guardar truena a media sentencia.
    c.q("alter table ops.ov_lineas drop constraint ov_lineas_linea_uq")
    c.q("alter table ops.ov_lineas add constraint ov_lineas_linea_uq unique (orden_id, linea)")
    return c.espera("KB000", c.migraciones["0064"])


@prueba("idempotencia", "paso0_stock_kubera_otro_tipo")
def _(c: Ctx):
    c.q("alter table ops.stock_watch_photo alter column stock_kubera type numeric")
    return c.espera("KB000", c.migraciones["0065"])


# ═══════════════════════════════════════════════════════════════════════════
# 3. POSITIVOS: los flujos del plan pasan y dejan todo cuadrado
# ═══════════════════════════════════════════════════════════════════════════
@prueba("positivo", "exigir")
def _(c: Ctx):
    c.afirma(c.valor("select ops.exigir(true, 'x')") == 1, "exigir(true) no devolvió 1")
    c.espera("KB001", "select ops.exigir(false, 'no_alcanzo')", mensaje="no_alcanzo")
    c.espera("KB001", "select ops.exigir(null, 'nulo_es_falso')", mensaje="nulo_es_falso")
    return "true → 1; false y NULL → KB001 con el motivo"


@prueba("positivo", "alta_borrador_y_folio")
def _(c: Ctx):
    antes = c.valor("select ultimo from ops.ov_folio")
    oid, folio, rev = c.ov([("ZZPRUEBA-A", 3, None), ("ZZPRUEBA-B", 1, "ENSAYO")], clave="panel:prueba-1")
    c.afirma(folio == f"OV-{antes + 1:05d}" and rev == 1, f"folio {folio} rev {rev}")
    c.afirma(len(c.lineas(oid)) == 2, "no nacieron los 2 renglones")
    c.afirma(c.valor("select count(*) from ops.ov_mensajes where orden_id = %s and evento = 'creada'", (oid,)) == 1,
             "falta el mensaje 'creada'")
    # Un alta con un CHECK roto truena con su nombre (que no consuma folio lo da la
    # sentencia única + el rollback, no una regla de la base: no se afirma aquí).
    c.cur.execute("savepoint alta_mala")
    try:
        c.ov([("ZZPRUEBA-A", 1, None)], canal="facebook")
        raise Falla("un alta con canal inválido no falló")
    except psycopg2.Error as e:
        c.cur.execute("rollback to savepoint alta_mala")
        c.afirma(e.pgcode == "23514" and e.diag.constraint_name == "ov_ordenes_canal_chk",
                 f"alta mala: {e.pgcode} {e.diag.constraint_name}")
    c.cur.execute("release savepoint alta_mala")
    c.inmediato()
    return f"{folio}, 2 renglones, mensaje creada; alta mala → 23514 ov_ordenes_canal_chk; diferidos OK"


@prueba("positivo", "guardar_borrador_llaves_diferidas")
def _(c: Ctx):
    oid, _, _ = c.ov([("ZZPRUEBA-A", 1, None), ("ZZPRUEBA-B", 2, None)])
    # Intercambiar `linea` en UNA sentencia: con una llave inmediata tronaría a media sentencia.
    c.q("update ops.ov_lineas set linea = case linea when 1 then 2 else 1 end where orden_id = %s", (oid,))
    # Quitar y volver a poner el mismo SKU en la misma transacción.
    lid = c.lineas(oid)[0][0]
    c.q("delete from ops.ov_lineas where id = %s", (lid,))
    c.q("insert into ops.ov_lineas (orden_id, linea, sku, cantidad) values (%s, 3, 'ZZPRUEBA-B', 5)", (oid,))
    c.q("update ops.ov_ordenes set descripcion = 'editada', rev = rev + 1 where id = %s", (oid,))
    c.inmediato()
    return "renumerar, quitar y agregar en borrador; unique NULLS NOT DISTINCT diferida OK"


@prueba("positivo", "formato_puerta_y_entrada_al_libro")
def _(c: Ctx):
    sku_a, sku_b = "ZZPRUEBA-FA", "ZZPRUEBA-FB"
    fid, folio, _ = c.uno(SQL_FORMATO_CARGA, {"alm": "ENSAYO", "hash": "a" * 64, "q": QUIEN, "lineas": json.dumps([
        {"sku": sku_a, "n": 7, "ubic": "A-01", "tex2": "7.0"}, {"sku": sku_a, "n": 3, "ubic": "A-02", "tex2": "7.0"},
        {"sku": sku_b, "n": 5, "ubic": None, "tex2": "6720.0"}])})
    c.afirma(re.fullmatch(r"FMT-\d{5,}", folio), f"folio generado raro: {folio}")
    c.afirma(c.saldo(sku_a) is None, "por confirmar ya tocó stock_almacen")
    c.q(SQL_FORMATO_CONFIRMAR, {"id": fid, "rev": 1, "q": QUIEN, "bodega": "Jefe de Bodega (prueba)",
                                "tex2": json.dumps({sku_a: "7.0", sku_b: "6720.0"})})
    c.afirma(c.valor("select odoo_tex2_al_confirmar from ops.stock_formato_linea where formato_id = %s and sku = %s",
                     (fid, sku_b)) == 6720, "odoo_tex2_al_confirmar no guardó el float de Odoo (H06)")
    # Confirmar dos veces = error con nombre.
    c.espera("KB001", SQL_FORMATO_CONFIRMAR, {"id": fid, "rev": 2, "q": QUIEN, "bodega": "x", "tex2": "{}"},
             mensaje="formato_no_esta_por_confirmar")
    # La puerta: solo sku_a salió de TEX2 en Odoo.
    abiertos, _ = c.uno(SQL_PUERTA, {"id": fid, "q": QUIEN, "listos": json.dumps(
        [{"sku": sku_a, "via": "tex2_cero", "ref": None, "tex2": "0.0"}])})
    c.afirma(abiertos == 1, f"la puerta abrió {abiertos} SKUs")
    c.afirma(c.saldo(sku_a) == (10, 0, 10), f"saldo de {sku_a}: {c.saldo(sku_a)}")
    c.afirma(c.saldo(sku_b) is None, f"{sku_b} entró sin su salida en Odoo")
    mov = c.uno("select delta, saldo_despues, ref, clave from ops.stock_mov where sku = %s and almacen = 'ENSAYO'", (sku_a,))
    c.afirma(mov == (10, 10, folio, f"fmt:{fid}:{sku_a}"), f"movimiento: {mov}")
    # Segunda pasada con los mismos SKUs: no abre nada y NO es error.
    abiertos2, _ = c.uno(SQL_PUERTA, {"id": fid, "q": QUIEN, "listos": json.dumps(
        [{"sku": sku_a, "via": "tex2_cero", "ref": None, "tex2": "0"}])})
    c.afirma(abiertos2 == 0, "la segunda pasada volvió a abrir")
    # Bajar la cantidad de un renglón que ESPERA, con nota y su candado optimista (rev).
    n = c.q("update ops.stock_formato_linea set cantidad = 4, nota = 'Bodega surtió 1 de TEX2', rev = rev + 1 "
            "where formato_id = %s and sku = %s and rev = 1 and salida_odoo_at is null returning id", (fid, sku_b))
    c.afirma(len(n) == 1, "no bajó la cantidad del renglón que espera")
    # Otra pantalla con la rev vieja: no encuentra la fila (0 filas), no pisa.
    n2 = c.q("update ops.stock_formato_linea set cantidad = 3, nota = 'Pantalla vieja: tomaron 2', rev = rev + 1 "
             "where formato_id = %s and sku = %s and rev = 1 and salida_odoo_at is null returning id", (fid, sku_b))
    c.afirma(not n2, "la pantalla vieja pisó la baja")
    c.inmediato()
    vista = c.q("select problema, sku from ops.stock_apartado_descuadre_v where sku in (%s, %s) or ref = %s",
                (sku_a, sku_b, folio))
    c.afirma(not vista, f"la vista vigía reporta: {vista}")
    return (f"{folio}: carga → confirmar → puerta (1 de 2) → libro 10 = saldo; 2a pasada abre 0; baja con rev "
            "y la pantalla vieja no pisa; vista limpia")


@prueba("positivo", "formato_dividir")
def _(c: Ctx):
    hash_ = "7" * 64
    pid, pfolio, _ = c.uno(SQL_FORMATO_CARGA, {"alm": "ENSAYO", "hash": hash_, "q": QUIEN, "lineas": json.dumps([
        {"sku": "ZZPRUEBA-DV1", "n": 5, "ubic": "A-01", "tex2": None},
        {"sku": "ZZPRUEBA-DV2", "n": 3, "ubic": "A-02", "tex2": None},
        {"sku": "ZZPRUEBA-DV3", "n": 2, "ubic": None, "tex2": None}])})
    # Bodega: DV2 y DV3 no están listos → pasan a un formato NUEVO por confirmar, del mismo archivo.
    hid, hfolio, _ = c.uno(SQL_FORMATO_DIVIDIR, {"id": pid, "q": QUIEN, "skus": ["ZZPRUEBA-DV2", "ZZPRUEBA-DV3"]})
    c.afirma(c.valor("select count(*) from ops.stock_formato_linea where formato_id = %s", (pid,)) == 1,
             "el padre conservó los renglones no listos")
    c.afirma(c.valor("select count(*) from ops.stock_formato_linea where formato_id = %s", (hid,)) == 2,
             "el hijo no recibió los 2 renglones")
    c.afirma(c.uno("select archivo_hash, dividido_de, estado from ops.stock_formato where id = %s", (hid,))
             == (hash_, pid, "por_confirmar"), "el hijo no es del mismo archivo o no nació por confirmar")
    # El padre se confirma con el hijo vivo; la puerta del padre abre solo lo suyo.
    c.q(SQL_FORMATO_CONFIRMAR, {"id": pid, "rev": 1, "q": QUIEN, "bodega": "Bodega (prueba)", "tex2": "{}"})
    abiertos, _ = c.uno(SQL_PUERTA, {"id": pid, "q": QUIEN, "listos": json.dumps(
        [{"sku": s, "via": "tex2_cero", "ref": None, "tex2": 0} for s in ("ZZPRUEBA-DV1", "ZZPRUEBA-DV2")])})
    c.afirma(abiertos == 1 and c.saldo("ZZPRUEBA-DV1") == (5, 0, 5) and c.saldo("ZZPRUEBA-DV2") is None,
             f"la puerta del padre abrió {abiertos}")
    r = [
        # El mismo archivo como formato NUEVO (no hijo) sigue chocando: el padre vive.
        c.espera("23505", SQL_FORMATO_CARGA, {"alm": "ENSAYO", "hash": hash_, "q": QUIEN, "lineas": json.dumps(
            [{"sku": "ZZPRUEBA-DV9", "n": 1, "ubic": None, "tex2": None}])}, constraint="stock_formato_hash_uq"),
        # Un «hijo» de otro archivo o de otra bodega: inventado.
        c.espera("23514", "insert into ops.stock_formato (almacen, archivo_nombre, archivo_hash, dividido_de, cargado_por) "
                          "values ('ENSAYO', 'x.xlsx', %s, %s, 'v')", ("8" * 64, pid), "stock_formato_dividido_hash_chk"),
        c.espera("23514", "insert into ops.stock_formato (almacen, archivo_nombre, archivo_hash, dividido_de, reemplaza_a, "
                          "cargado_por) values ('ENSAYO', 'x.xlsx', %s, %s, %s, 'v')", (hash_, pid, pid),
                 "stock_formato_dividido_chk"),
        c.espera("42501", "update ops.stock_formato set dividido_de = null where id = %s", (hid,), "stock_formato_inmutable"),
    ]
    c.q(SQL_FORMATO_CONFIRMAR, {"id": hid, "rev": 1, "q": QUIEN, "bodega": "Bodega (prueba)", "tex2": "{}"})
    c.inmediato()
    return f"{pfolio} → dividido en {hfolio} (mismo hash); padre confirmado con el hijo vivo; {len(r)} rechazos"


@prueba("positivo", "confirmar_aparta_y_saldo")
def _(c: Ctx):
    sku = "ZZPRUEBA-C1"
    c.entrada(sku, 10)
    oid, folio, rev = c.ov([(sku, 4, "ENSAYO")], clave="panel:conf-1")
    s, p = c.confirmar(oid)
    c.afirma(c.valor(s, p) == 4, "confirmar no cuadró")
    c.afirma(c.saldo(sku) == (10, 4, 6), f"saldo: {c.saldo(sku)}")
    c.afirma(c.lineas(oid)[0][4] == 4, "el renglón no quedó apartado completo")
    c.afirma(c.valor("select estado from ops.ov_ordenes where id = %s", (oid,)) == "confirmada", "no quedó confirmada")
    # Doble clic: la segunda confirmación no encuentra el borrador con esa rev.
    s2, p2 = c.confirmar(oid, rev=rev)
    c.espera("KB001", s2, p2, mensaje="ov_no_esta_en_borrador_o_cambio_rev")
    c.inmediato()
    return f"{folio}: fisico 10, apartado 4, libre 6; doble clic → KB001"


@prueba("positivo", "confirmar_no_alcanza_nada_aparta")
def _(c: Ctx):
    sku1, sku2 = "ZZPRUEBA-N1", "ZZPRUEBA-N2"
    c.entrada(sku1, 5)
    c.entrada(sku2, 1)
    oid, _, _ = c.ov([(sku1, 2, "ENSAYO"), (sku2, 3, "ENSAYO")])
    s, p = c.confirmar(oid)
    # Lo que prueba la base es el KB001 con su motivo; «nada quedó apartado» lo
    # garantiza que sea UNA sentencia (y el ROLLBACK TO del SAVEPOINT), no se afirma.
    c.espera("KB001", s, p, mensaje="no_alcanzo")
    c.inmediato()
    return "un SKU sin piezas → KB001 no_alcanzo (todo o nada en una sentencia)"


@prueba("positivo", "entregar_parcial_y_total")
def _(c: Ctx):
    sku1, sku2 = "ZZPRUEBA-E1", "ZZPRUEBA-E2"
    c.entrada(sku1, 10)
    c.entrada(sku2, 10)
    oid, folio, _ = c.ov([(sku1, 3, "ENSAYO"), (sku2, 2, "ENSAYO")])
    s, p = c.confirmar(oid)
    c.q(s, p)
    l1, l2 = c.lineas(oid)[0][0], c.lineas(oid)[1][0]
    s, p = c.entregar(oid, [(l1, 3)])
    c.q(s, p)
    c.afirma(c.valor("select estado from ops.ov_ordenes where id = %s", (oid,)) == "confirmada", "pasó a entregada antes")
    c.afirma(c.saldo(sku1) == (7, 0, 7), f"saldo {sku1}: {c.saldo(sku1)}")
    s, p = c.entregar(oid, [(l2, 1)])      # sale menos de lo pedido: lo demás se suelta
    c.q(s, p)
    c.afirma(c.valor("select estado from ops.ov_ordenes where id = %s", (oid,)) == "entregada", "no quedó entregada")
    c.afirma(c.saldo(sku2) == (9, 0, 9), f"saldo {sku2}: {c.saldo(sku2)}")
    c.afirma(c.valor("select clave from ops.stock_mov where ov_linea_id = %s", (l1,)) == f"ov:{oid}:linea:{l1}:salida",
             "clave de salida distinta")
    c.inmediato()
    return f"{folio}: parcial → total; libro y saldos cuadran"


@prueba("positivo", "cancelar_suelta_el_apartado")
def _(c: Ctx):
    sku = "ZZPRUEBA-K1"
    oid, _ = c.ov_confirmada(sku, 4, fisico=6)
    s, p = c.cancelar(oid, motivo="El cliente ya no la quiere")
    c.q(s, p)
    c.afirma(c.saldo(sku) == (6, 0, 6), f"saldo tras cancelar: {c.saldo(sku)}")
    c.afirma(c.lineas(oid)[0][4] == 0, "el renglón conservó reservado")
    c.inmediato()
    # La clave de una cancelada se libera (ov_ordenes_clave_uq es parcial): con una
    # cancelación en medio, la misma clave vuelve a entrar.
    o1, _, _ = c.ov([(sku, 1, "ENSAYO")], clave="panel:reusable")
    s, p = c.cancelar(o1, motivo="Se rehace con la misma clave")
    c.q(s, p)
    o2, _, _ = c.ov([(sku, 1, "ENSAYO")], clave="panel:reusable")
    c.afirma(o2 != o1, "la segunda alta con la clave liberada no creó otra OV")
    c.inmediato()
    return "cancelar devuelve el apartado exacto; la clave de una cancelada se reutiliza"


@prueba("positivo", "salio_canal_cancelo_con_paquete_enviado")
def _(c: Ctx):
    sku = "ZZPRUEBA-S1"
    oid, _ = c.ov_confirmada(sku, 2, fisico=5)
    c.q("""with o as (update ops.ov_ordenes set canal_cancelo_at = now(), canal_cancelo_ref = 'IN_TRANSIT', rev = rev + 1
                      where id = %(id)s and estado = 'confirmada' returning id)
           insert into ops.ov_mensajes (orden_id, tipo, evento, cuerpo, autor, via)
           select id, 'sistema', 'canal_cancelo', 'El canal canceló con el paquete en tránsito: ¿salió?', 'automatico', 'automatico' from o""",
        {"id": oid})
    c.afirma(c.saldo(sku) == (5, 2, 3), "la marca soltó el apartado")
    # Bodega: SÍ salió → entrega + entregada_cancelada + devolución pendiente, en una sentencia.
    lid = c.lineas(oid)[0][0]
    c.q("""
        with o as (
          update ops.ov_ordenes v
             set estado = 'entregada_cancelada', entregada_at = now(), entregada_por = %(q)s,
                 cancelada_at = now(), cancelada_por = 'automatico', cancelada_origen = 'marketplace',
                 cancelada_motivo = 'Cancelada en el canal con el paquete ya enviado',
                 devolucion_estado = 'pendiente', rev = v.rev + 1
           where v.id = %(id)s and v.estado = 'confirmada' and v.canal_cancelo_at is not null and v.borrada_at is null
          returning v.id
        ), x as materialized (
          select li.id as linea_id, sa.sku, sa.almacen, li.cantidad
            from o join ops.ov_lineas li on li.orden_id = o.id and li.entregado_at is null and li.reservado = li.cantidad
            join ops.stock_almacen sa on sa.sku = li.sku and sa.almacen = li.almacen
           order by sa.sku, sa.almacen for update of sa
        ), s as (
          update ops.stock_almacen sa set fisico = sa.fisico - x.cantidad, apartado = sa.apartado - x.cantidad
            from x where sa.sku = x.sku and sa.almacen = x.almacen
          returning sa.sku, sa.almacen, sa.fisico, x.cantidad as n, x.linea_id
        ), m as (
          insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ov_linea_id, clave, quien, via)
          select s.sku, s.almacen, -s.n, s.fisico, 'salida_ov', s.linea_id,
                 'ov:' || %(id)s || ':linea:' || s.linea_id || ':salida', %(q)s, 'prueba' from s
          returning id
        ), r as (
          update ops.ov_lineas li set reservado = 0, entregado = s.n, entregado_at = now(), entregado_por = %(q)s
            from s where li.id = s.linea_id returning li.id
        )
        select ops.exigir((select count(*) from o) = 1 and (select count(*) from m) = (select count(*) from x)
                          and (select count(*) from r) = (select count(*) from x), 'salio_no_cuadra')
        """, {"id": oid, "q": QUIEN})
    c.afirma(c.saldo(sku) == (3, 0, 3), f"saldo tras salir: {c.saldo(sku)}")
    c.afirma(c.valor("select clave from ops.stock_mov where ov_linea_id = %s", (lid,)) == f"ov:{oid}:linea:{lid}:salida",
             "la clave de «¿salió?» no es la de entregar")
    c.inmediato()
    return "marca → no suelta → salió: entregada_cancelada, devolución pendiente, misma clave que entregar"


@prueba("positivo", "devolucion_recibir_y_vendible")
def _(c: Ctx):
    sku = "ZZPRUEBA-D1"
    oid, folio = c.ov_confirmada(sku, 3, fisico=3)
    lid = c.lineas(oid)[0][0]
    s, p = c.entregar(oid, [(lid, 3)])
    c.q(s, p)
    dev_id, _ = c.uno(SQL_DEV_RECIBIR, {"linea": lid, "n": 1, "paq": "4471-2290 11", "ids": ["4471229011"],
                                        "clave": "pantalla:7f3c-0001", "q": QUIEN})
    c.afirma(c.saldo(sku, "REVISION") == (1, 0, 1), f"REVISION: {c.saldo(sku, 'REVISION')}")
    c.afirma(c.valor("select devolucion_estado from ops.ov_ordenes where id = %s", (oid,)) == "recibida",
             "la OV entregada no pasó a 'recibida'")
    # Recapturar más de lo entregado: la guarda de la sentencia la frena.
    c.espera("KB001", SQL_DEV_RECIBIR, {"linea": lid, "n": 3, "paq": "4471-2290 11", "ids": ["4471229011"],
                                        "clave": "pantalla:7f3c-0002", "q": QUIEN},
             mensaje="devolucion_de_mas_o_sin_venta")
    c.q(SQL_DEV_VENDIBLE, {"id": dev_id, "q": QUIEN})
    c.afirma(c.saldo(sku, "REVISION") == (0, 0, 0), "no salió de REVISION")
    c.afirma(c.saldo(sku) == (1, 0, 1), f"no volvió a ENSAYO: {c.saldo(sku)}")
    c.inmediato()
    vista = c.q("select problema from ops.stock_apartado_descuadre_v where sku = %s", (sku,))
    c.afirma(not vista, f"vista vigía: {vista}")
    return f"{folio}: devolución +1 en REVISION, 'recibida', recaptura frenada, vendible → traspaso a ENSAYO"


@prueba("positivo", "devolucion_llave_de_recepcion_y_partida")
def _(c: Ctx):
    sku = "ZZPRUEBA-D2"
    oid, folio = c.ov_confirmada(sku, 4, fisico=4)
    lid = c.lineas(oid)[0][0]
    s, p = c.entregar(oid, [(lid, 4)])
    c.q(s, p)
    base = {"linea": lid, "paq": "GUIA-B 77 / GUIA-A 12", "q": QUIEN}
    d1, _ = c.uno(SQL_DEV_RECIBIR, dict(base, n=2, ids=["GUIAB77", "GUIAA12"], clave="pantalla:rc-0001"))
    c.afirma(c.valor("select paquete_ids from ops.devoluciones where id = %s", (d1,)) == ["GUIAA12", "GUIAB77"],
             "paquete_ids no quedó ordenado")
    # Otro turno, otra pantalla, la MISMA caja (ids en otro orden): Σ 2 + 1 ≤ 4 pasa la
    # suma, y la llave de recepción la frena (23505 = ya recibida).
    r = [c.espera("23505", SQL_DEV_RECIBIR, dict(base, n=1, ids=["GUIAA12", "GUIAB77"], clave="pantalla:rc-0002"),
                  constraint="devoluciones_recepcion_uq")]
    # Partir el paquete por dictamen: la fila baja y nace otra con partida_de (sin 'recibe').
    c.q("""with o as (update ops.devoluciones set cantidad = cantidad - 1, rev = rev + 1 where id = %(d)s and cantidad > 1
                      returning id, origen, sku, ov_linea_id, paquete_ref, paquete_ids, empaque)
           insert into ops.devoluciones (origen, sku, cantidad, ov_linea_id, paquete_ref, paquete_ids, empaque,
                                         recibido_por, clave, partida_de, dictamen, dictamen_at, dictamen_por)
           select o.origen, o.sku, 1, o.ov_linea_id, o.paquete_ref, o.paquete_ids, o.empaque, %(q)s, 'pantalla:rc-0003',
                  o.id, 'reparar', now(), %(q)s from o""", {"d": d1, "q": QUIEN})
    # Otra caja del mismo renglón: sí entra.
    c.uno(SQL_DEV_RECIBIR, dict(base, n=1, ids=["GUIAC99"], clave="pantalla:rc-0004"))
    # Una «partida» de OTRO paquete: no es partida.
    r.append(c.espera("23514", """insert into ops.devoluciones (origen, sku, cantidad, ov_linea_id, paquete_ref, paquete_ids,
                                    empaque, recibido_por, clave, partida_de)
                                  values ('venta', %s, 1, %s, 'GUIA-Z', '{GUIAZ}', 'cerrado', 'v', 'pantalla:rc-0005', %s)""",
                      (sku, lid, d1), "devoluciones_partida_igual_chk"))
    r.append(c.espera("42501", "update ops.devoluciones set partida_de = null where partida_de = %s", (d1,),
                      "devoluciones_inmutable"))
    c.inmediato()
    vista = c.q("select problema from ops.stock_apartado_descuadre_v where sku = %s", (sku,))
    c.afirma(not vista and c.saldo(sku, "REVISION") == (3, 0, 3), f"vista {vista}, REVISION {c.saldo(sku, 'REVISION')}")
    return (f"{folio}: misma caja (otro orden) → 23505 devoluciones_recepcion_uq; partida del mismo paquete sí; "
            f"otra caja sí; {len(r)} rechazos; REVISION 3 = abiertas 3")


@prueba("positivo", "salio_tarde_de_una_cancelada")
def _(c: Ctx):
    sku = "ZZPRUEBA-ST"
    oid, folio = c.ov_confirmada(sku, 2, fisico=5)
    s, p = c.cancelar(oid, motivo="TikTok canceló en AWAITING_SHIPMENT")
    c.q(s, p)
    c.afirma(c.saldo(sku) == (5, 0, 5), f"cancelar no soltó: {c.saldo(sku)}")
    # La caja SÍ había salido: la pantalla registra la salida (decisión 15 de la 0064).
    c.q(SQL_SALIO_TARDE, {"id": oid, "q": QUIEN})
    c.afirma(c.saldo(sku) == (3, 0, 3), f"saldo tras la salida tardía: {c.saldo(sku)}")
    fila = c.uno("select estado, devolucion_estado, cancelada_motivo from ops.ov_ordenes where id = %s", (oid,))
    c.afirma(fila == ("entregada_cancelada", "pendiente", "TikTok canceló en AWAITING_SHIPMENT"), f"OV: {fila}")
    lid = c.lineas(oid)[0][0]
    c.uno(SQL_DEV_RECIBIR, {"linea": lid, "n": 2, "paq": "GUIA-ST 01", "ids": ["GUIAST01"],
                            "clave": "pantalla:st-0001", "q": QUIEN})
    c.afirma(c.saldo(sku, "REVISION") == (2, 0, 2), f"REVISION: {c.saldo(sku, 'REVISION')}")
    c.inmediato()
    # Un borrador cancelado nunca salió: no llega a entregada_cancelada.
    oid2, _, _ = c.ov([("ZZPRUEBA-ST2", 1, "ENSAYO")])
    c.entrada("ZZPRUEBA-ST2", 1)
    s, p = c.cancelar(oid2, motivo="Borrador que no siguió")
    c.q(s, p)
    r = c.espera("23514", SQL_SALIO_TARDE, {"id": oid2, "q": QUIEN}, constraint="ov_ordenes_conf_chk")
    return f"{folio}: cancelada → salió (entregada_cancelada, −2 en ENSAYO) → devolución a REVISION; borrador → {r}"


@prueba("positivo", "crear_auto_idempotente")
def _(c: Ctx):
    sku = "ZZPRUEBA-AU"
    c.entrada(sku, 5)
    p = {"alm": "ENSAYO", "clave": "mp:tiktok:CUENTAPRUEBA:5770001", "mc": "tiktok", "mu": "CUENTAPRUEBA",
         "mo": "5770001", "lineas": json.dumps([{"sku": sku, "n": 2}])}
    # ENSAYO no surte ventas: crear_auto no aparta ahí (C11) → KB001 con su motivo.
    c.espera("KB001", SQL_CREAR_AUTO, p, mensaje="no_alcanzo")
    # Encenderla para la prueba, con acta (motivo y quién), como la fase B.
    c.q("select set_config('app.usuario', 'verificador@prueba', true)")
    c.q("update ops.almacenes set surte_ventas = true, preferencia = 9, "
        "motivo = 'Prueba: ENSAYO surte ventas (crear_auto)' where codigo = 'ENSAYO'")
    oid, res, _ = c.uno(SQL_CREAR_AUTO, p)
    c.afirma(res == "creada", res)
    c.afirma(c.saldo(sku) == (5, 2, 3), f"saldo: {c.saldo(sku)}")
    oid2, res2, _ = c.uno(SQL_CREAR_AUTO, p)
    c.afirma(res2 == "ya_existia" and oid2 == oid, f"la segunda: {res2} {oid2}")
    # Otra clave, la MISMA venta: `previa` también busca por mp_* (C1).
    oid3, res3, _ = c.uno(SQL_CREAR_AUTO, dict(p, clave="mp:otra-clave"))
    c.afirma(res3 == "ya_existia" and oid3 == oid, f"por la venta: {res3}")
    c.afirma(c.saldo(sku) == (5, 2, 3), "apartó dos veces")
    c.afirma(c.valor("select creado_via || '/' || cliente from ops.ov_ordenes where id = %s", (oid,)) == "automatico/tiktok",
             "la automática no quedó con cliente = canal")
    c.inmediato()
    return ("bodega sin surte_ventas → KB001 no_alcanzo; crea confirmada y apartada; "
            "repetida (por clave o por venta) → ya_existia sin apartar de nuevo")


@prueba("positivo", "conteo_deja_libre_negativo_y_la_vista_avisa")
def _(c: Ctx):
    sku = "ZZPRUEBA-CT"
    oid, _ = c.ov_confirmada(sku, 4, fisico=5)
    c.q(SQL_CONTEO, {"sku": sku, "alm": "ENSAYO", "visto": 5, "contado": 3, "q": QUIEN})
    c.afirma(c.saldo(sku) == (3, 4, -1), f"saldo tras el conteo: {c.saldo(sku)}")
    c.inmediato()   # el libro cuadra: el conteo escribió su movimiento
    vista = c.q("select problema, encontrado from ops.stock_apartado_descuadre_v where sku = %s", (sku,))
    c.afirma(("libre_negativo", -1) in vista, f"la vista no avisó el libre negativo: {vista}")
    # Contar contra lo que ya cambió: se pide recontar.
    c.espera("KB001", SQL_CONTEO, {"sku": sku, "alm": "ENSAYO", "visto": 5, "contado": 2, "q": QUIEN}, mensaje="recontar")
    return "conteo legítimo deja libre −1 (permitido) y la vista lo reporta; conteo viejo → recontar"


@prueba("positivo", "almacenes_cambio_con_acta")
def _(c: Ctx):
    c.q("select set_config('app.usuario', 'verificador@prueba', true)")
    c.q("update ops.almacenes set admite_ov = true, motivo = 'Prueba: encender OV manuales en TEX3' where codigo = 'TEX3'")
    fila = c.uno("select quien, motivo, (antes->>'admite_ov')::boolean, (despues->>'admite_ov')::boolean "
                 "from ops.almacenes_hist where codigo = 'TEX3' order by id desc limit 1")
    c.afirma(fila == ("verificador@prueba", "Prueba: encender OV manuales en TEX3", False, True), f"historia: {fila}")
    # Intercambiar preferencias en UN update (almacenes_pref_uq diferible, H15).
    c.q("update ops.almacenes set preferencia = case codigo when 'TEXCO' then 2 else 1 end, "
        "motivo = 'Prueba: TEX2 antes que TEXCO (D2)' where codigo in ('TEXCO', 'TEX2')")
    # La fase B, como debe ir: admite_ov en el MISMO UPDATE que surte_ventas y Woo.
    c.q("update ops.almacenes set admite_ov = true, surte_ventas = true, cuenta_para_woo = true, "
        "motivo = 'Prueba: fase B de TEX3 con admite_ov' where codigo = 'TEX3'")
    # Alta de una bodega con acta.
    c.q("insert into ops.almacenes (codigo, nombre, fuente, motivo) values ('TEX4', 'Nave 4', 'kubera', "
        "'Prueba: alta de una nave con acta')")
    alta = c.uno("select quien, motivo from ops.almacenes_hist where codigo = 'TEX4' and antes is null")
    c.afirma(alta == ("verificador@prueba", "Prueba: alta de una nave con acta"), f"alta sin rastro: {alta}")
    c.q("select set_config('app.usuario', '', true)")
    return ("cambio con motivo y app.usuario → historia con antes/después; intercambio de preferencias; "
            "fase B con admite_ov; alta con acta")


# ═══════════════════════════════════════════════════════════════════════════
# 4. NEGATIVOS: lo que la base tiene que rechazar, con su SQLSTATE
# ═══════════════════════════════════════════════════════════════════════════
@prueba("negativo", "checks_de_ov")
def _(c: Ctx):
    oid, _, _ = c.ov([("ZZPRUEBA-X", 2, None)])
    r = []
    r.append(c.espera("23514", "update ops.ov_ordenes set mp_orden = '123' where id = %s", (oid,), "ov_ordenes_mp_chk"))
    r.append(c.espera("23514", "update ops.ov_ordenes set mp_canal = 'TikTok', mp_cuenta = 'X', mp_orden = '1' where id = %s",
                      (oid,), "ov_ordenes_mp_forma_chk"))
    r.append(c.espera("23514", "update ops.ov_ordenes set canal_cancelo_at = now(), canal_cancelo_ref = 'IN_TRANSIT' where id = %s",
                      (oid,), "ov_ordenes_canal_cancelo_chk"))
    r.append(c.espera("23514", "update ops.ov_ordenes set tipo = 'full', full_tienda = 'amazon', canal = 'walmart' where id = %s",
                      (oid,), "ov_ordenes_full_tc_chk"))
    r.append(c.espera("23514", "update ops.ov_ordenes set canal = 'facebook' where id = %s", (oid,), "ov_ordenes_canal_chk"))
    r.append(c.espera("23514", "update ops.ov_ordenes set devolucion_estado = 'pendiente' where id = %s", (oid,),
                      "ov_ordenes_devol_chk"))
    # Automática con el comprador como cliente (SEG-06).
    r.append(c.espera("23514", """insert into ops.ov_ordenes (folio, estado, cliente, canal, mp_canal, mp_cuenta, mp_orden,
                                   creado_por, creado_via) values ('OV-99999', 'borrador', 'Juana Pérez', 'tiktok', 'tiktok',
                                   'CUENTA', '1', 'automatico', 'automatico')""", constraint="ov_ordenes_auto_cliente_chk"))
    r.append(c.espera("23514", "update ops.ov_lineas set reservado = 1, almacen = 'ENSAYO' where orden_id = %s", (oid,),
                      "ov_lineas_reservado_chk"))
    r.append(c.espera("23514", """insert into ops.ov_archivos (orden_id, tipo, nombre, ruta, sha256, bytes, subido_por)
                                  values (%s, 'etiqueta', 'x.pdf', 'x', %s, 10, 'v')""", (oid, "b" * 64), "ov_archivos_tipo_chk"))
    r.append(c.espera("23514", """insert into ops.ov_archivos (orden_id, tipo, nombre, ruta, sha256, bytes, subido_por, borrado_at)
                                  values (%s, 'comprobante', 'x.pdf', 'x', %s, 10, 'v', now())""", (oid, "c" * 64),
                      "ov_archivos_borrado_chk"))
    r.append(c.espera("23514", "insert into ops.ov_mensajes (orden_id, tipo, evento, cuerpo, autor) values (%s, 'sistema', 'inventado', 'x', 'v')",
                      (oid,), "ov_mensajes_evento_chk"))
    return f"{len(r)} CHECK con su nombre"


@prueba("negativo", "unicos_parciales")
def _(c: Ctx):
    c.ov([("ZZPRUEBA-U", 1, None)], clave="panel:dup")
    r = [c.espera("23505", c.ov.__func__ and """
            insert into ops.ov_ordenes (folio, estado, creado_por, clave) values ('OV-99998', 'borrador', 'v', 'panel:dup')""",
                  constraint="ov_ordenes_clave_uq")]
    mp = ("tiktok", "CUENTA", "88001")
    oid, _, _ = c.ov([("ZZPRUEBA-U", 1, None)], mp=mp, canal="tiktok", cliente="tiktok")
    r.append(c.espera("23505", """insert into ops.ov_ordenes (folio, estado, creado_por, mp_canal, mp_cuenta, mp_orden)
                                  values ('OV-99997', 'borrador', 'v', 'tiktok', 'CUENTA', '88001')""",
                      constraint="ov_ordenes_mp_uq"))
    # Cancelada la primera, la venta puede tener otra OV viva (índice parcial).
    s, p = c.cancelar(oid)
    c.q(s, p)
    c.ov([("ZZPRUEBA-U", 1, None)], mp=mp, canal="tiktok", cliente="tiktok")
    # Formato: el mismo archivo dos veces, mientras siga vivo.
    c.q(SQL_FORMATO_CARGA, {"alm": "ENSAYO", "hash": "d" * 64, "q": QUIEN,
                            "lineas": json.dumps([{"sku": "ZZPRUEBA-U", "n": 1, "ubic": None, "tex2": None}])})
    r.append(c.espera("23505", SQL_FORMATO_CARGA, {"alm": "ENSAYO", "hash": "d" * 64, "q": QUIEN, "lineas": json.dumps(
        [{"sku": "ZZPRUEBA-U", "n": 1, "ubic": None, "tex2": None}])}, constraint="stock_formato_hash_uq"))
    # Renglón repetido (orden, sku, bodega NULL): la llave diferida truena al COMMIT.
    oid2, _, _ = c.ov([("ZZPRUEBA-R", 1, None)])
    r.append(c.espera("23505", [("insert into ops.ov_lineas (orden_id, linea, sku, cantidad) values (%s, 2, 'ZZPRUEBA-R', 1)",
                                 (oid2,))], constraint="ov_lineas_sku_alm_uq", al_commit=True))
    # Una sola salida por renglón y clave única del libro.
    sku = "ZZPRUEBA-UL"
    oid3, _ = c.ov_confirmada(sku, 1, fisico=2)
    lid = c.lineas(oid3)[0][0]
    s, p = c.entregar(oid3, [(lid, 1)])
    c.q(s, p)
    r.append(c.espera("23505", """insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ov_linea_id, clave, quien)
                                  values (%s, 'ENSAYO', -1, 0, 'salida_ov', %s, %s, 'v')""",
                      (sku, lid, f"ov:{oid3}:linea:{lid + 100000}:salida"), constraint="stock_mov_salida_ov_uq"))
    r.append(c.espera("23505", """insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, clave, quien, ref)
                                  values (%s, 'ENSAYO', 1, 2, 'entrada', (select clave from ops.stock_mov where sku = %s
                                  and motivo = 'entrada' limit 1), 'v', 'x')""", (sku, sku), constraint="stock_mov_clave_uq"))
    return f"{len(r)} únicos (clave, venta, archivo, renglón diferido, salida, clave del libro); cancelada libera la venta"


@prueba("negativo", "solo_agregar")
def _(c: Ctx):
    sku = "ZZPRUEBA-SA"
    c.entrada(sku, 3)
    oid, _, _ = c.ov([(sku, 1, None)])
    fid, _, _ = c.uno(SQL_FORMATO_CARGA, {"alm": "ENSAYO", "hash": "e" * 64, "q": QUIEN,
                                          "lineas": json.dumps([{"sku": sku, "n": 1, "ubic": None, "tex2": None}])})
    # La entrada dejó eventos diferidos pendientes en stock_mov; con ellos,
    # Postgres rechaza el TRUNCATE con 55006 ANTES de llegar al trigger. Se
    # disparan ya para probar el trigger mismo.
    c.inmediato()
    casos = [
        ("update ops.stock_mov set delta = 99 where sku = %s", (sku,), "stock_mov_solo_agregar"),
        ("delete from ops.stock_mov where sku = %s", (sku,), "stock_mov_solo_agregar"),
        ("truncate ops.stock_mov", None, "stock_mov_solo_agregar"),
        ("update ops.ov_mensajes set cuerpo = 'editado' where orden_id = %s", (oid,), "ov_mensajes_solo_agregar"),
        ("delete from ops.ov_mensajes where orden_id = %s", (oid,), "ov_mensajes_solo_agregar"),
        ("truncate ops.ov_mensajes cascade", None, "ov_mensajes_solo_agregar"),
        ("update ops.stock_formato_evento set nota = 'x' where formato_id = %s", (fid,), "stock_formato_evento_solo_agregar"),
        ("delete from ops.almacenes_hist", None, "almacenes_hist_solo_agregar"),
        ("update ops.migraciones set quien = 'x'", None, "migraciones_solo_agregar"),
        ("truncate ops.migraciones", None, "migraciones_solo_agregar"),
    ]
    for s, p, nombre in casos:
        c.espera("42501", s, p, constraint=nombre)
    return f"{len(casos)} UPDATE/DELETE/TRUNCATE rechazados (42501) en las 5 tablas de solo agregar"


@prueba("negativo", "inmutabilidad_de_lo_confirmado")
def _(c: Ctx):
    sku = "ZZPRUEBA-IM"
    oid, _ = c.ov_confirmada(sku, 2, fisico=4)
    lid = c.lineas(oid)[0][0]
    r = [
        c.espera("42501", "update ops.ov_ordenes set descripcion = 'cambiada' where id = %s", (oid,), "ov_ordenes_inmutable"),
        c.espera("42501", "update ops.ov_ordenes set total = 999 where id = %s", (oid,), "ov_ordenes_inmutable"),
        c.espera("42501", "update ops.ov_ordenes set guia = 'G1' where id = %s", (oid,), "ov_ordenes_inmutable"),
        c.espera("42501", "update ops.ov_ordenes set folio = 'OV-77777' where id = %s", (oid,), "ov_ordenes_inmutable"),
        c.espera("42501", "delete from ops.ov_ordenes where id = %s", (oid,), "ov_ordenes_sin_borrado"),
        c.espera("42501", "update ops.ov_lineas set cantidad = 5 where id = %s", (lid,), "ov_lineas_inmutable"),
        c.espera("42501", "update ops.ov_lineas set sku = 'OTRO' where id = %s", (lid,), "ov_lineas_inmutable"),
        c.espera("42501", "delete from ops.ov_lineas where id = %s", (lid,), "ov_lineas_inmutable"),
    ]
    # Agregar un renglón a una OV confirmada de ANTES de esta transacción. OV-99996
    # (confirmada y sin renglones) deja pendiente su propio ov_ordenes_coherente:
    # va en su SAVEPOINT para que el caso al COMMIT de abajo solo pueda fallar
    # por verificar_ov(oid).
    c.cur.execute("savepoint viejo")
    viejo = c.uno("""insert into ops.ov_ordenes (folio, estado, creado_por, creado_via, confirmada_at, confirmada_por, creado_at)
                     values ('OV-99996', 'confirmada', 'automatico', 'automatico', now(), 'automatico',
                             now() - interval '1 day') returning id""")[0]
    r.append(c.espera("42501", "insert into ops.ov_lineas (orden_id, linea, sku, cantidad) values (%s, 1, 'ZZPRUEBA-IM2', 1)",
                      (viejo,), "ov_lineas_inmutable"))
    c.cur.execute("rollback to savepoint viejo")
    c.cur.execute("release savepoint viejo")
    # Un renglón SIN apartar en una OV confirmada de esta transacción: lo frena la coherencia al COMMIT.
    r.append(c.espera("23514", [("insert into ops.ov_lineas (orden_id, linea, sku, cantidad) values (%s, 9, 'ZZPRUEBA-IM3', 1)",
                                 (oid,))], constraint="ov_coherente", al_commit=True))
    # Lo permitido sí pasa: la marca «¿salió?».
    c.q("update ops.ov_ordenes set canal_cancelo_at = now(), canal_cancelo_ref = 'shipped' where id = %s", (oid,))
    r.append(c.espera("42501", "update ops.ov_ordenes set canal_cancelo_ref = 'otro' where id = %s", (oid,),
                      "ov_ordenes_inmutable"))
    # Formato confirmado y renglón con la puerta abierta.
    fid, _, _ = c.uno(SQL_FORMATO_CARGA, {"alm": "ENSAYO", "hash": "f" * 64, "q": QUIEN, "lineas": json.dumps(
        [{"sku": "ZZPRUEBA-IF", "n": 4, "ubic": None, "tex2": None}, {"sku": "ZZPRUEBA-IG", "n": 2, "ubic": None, "tex2": None}])})
    c.q(SQL_FORMATO_CONFIRMAR, {"id": fid, "rev": 1, "q": QUIEN, "bodega": "Bodega", "tex2": "{}"})
    c.q(SQL_PUERTA, {"id": fid, "q": QUIEN, "listos": json.dumps([{"sku": "ZZPRUEBA-IF", "via": "movimiento",
                                                                    "ref": "TEX2/INT/00123", "tex2": 0}])})
    r.append(c.espera("42501", "update ops.stock_formato set archivo_nombre = 'otro.xlsx' where id = %s", (fid,),
                      "stock_formato_inmutable"))
    r.append(c.espera("42501", "delete from ops.stock_formato where id = %s", (fid,), "stock_formato_sin_borrado"))
    r.append(c.espera("42501", "update ops.stock_formato_linea set cantidad = 3, nota = 'bajar abierta' where formato_id = %s "
                      "and sku = 'ZZPRUEBA-IF'", (fid,), "stock_formato_linea_inmutable"))
    r.append(c.espera("23514", "update ops.stock_formato_linea set cantidad = 3 where formato_id = %s and sku = 'ZZPRUEBA-IG'",
                      (fid,), "stock_formato_linea_solo_baja"))
    r.append(c.espera("23514", "update ops.stock_formato_linea set cantidad = 1 where formato_id = %s and sku = 'ZZPRUEBA-IG'",
                      (fid,), "stock_formato_linea_nota_chk"))
    # Bajar sin subir rev (sin candado optimista) y saltarse revs: rechazados.
    r.append(c.espera("23514", "update ops.stock_formato_linea set cantidad = 1, nota = 'Bodega tomó una pieza' "
                               "where formato_id = %s and sku = 'ZZPRUEBA-IG'", (fid,), "stock_formato_linea_rev_chk"))
    r.append(c.espera("23514", "update ops.stock_formato_linea set rev = rev + 2 where formato_id = %s and sku = 'ZZPRUEBA-IG'",
                      (fid,), "stock_formato_linea_rev_chk"))
    r.append(c.espera("42501", "update ops.stock_formato_linea set ubicacion = 'B-07' where formato_id = %s and sku = 'ZZPRUEBA-IG'",
                      (fid,), "stock_formato_linea_inmutable"))
    r.append(c.espera("42501", "delete from ops.stock_formato_linea where formato_id = %s and sku = 'ZZPRUEBA-IG'", (fid,),
                      "stock_formato_linea_inmutable"))
    r.append(c.espera("42501", """insert into ops.stock_formato_linea (formato_id, fila, sku, sku_archivo, cantidad, cantidad_archivo)
                                  values (%s, 9, 'ZZPRUEBA-IH', 'ZZPRUEBA-IH', 1, 1)""", (fid,), "stock_formato_linea_inmutable"))
    return f"{len(r)} ediciones de lo confirmado rechazadas; la marca «¿salió?» sí pasa y después no cambia"


@prueba("negativo", "transiciones_invalidas")
def _(c: Ctx):
    oid, _, _ = c.ov([("ZZPRUEBA-T", 1, None)])
    r = [
        c.espera("23514", "update ops.ov_ordenes set estado = 'entregada', entregada_at = now(), entregada_por = 'v', "
                          "confirmada_at = now(), confirmada_por = 'v' where id = %s", (oid,), "ov_ordenes_transicion_chk"),
        c.espera("23514", "insert into ops.ov_ordenes (folio, estado, creado_por, confirmada_at, confirmada_por, entregada_at, "
                          "entregada_por) values ('OV-99995', 'entregada', 'v', now(), 'v', now(), 'v')",
                 constraint="ov_ordenes_transicion_chk"),
        # Solo crear_auto hace nacer una OV confirmada: el panel pasa por confirmar.
        c.espera("23514", "insert into ops.ov_ordenes (folio, estado, creado_por, creado_via, confirmada_at, confirmada_por) "
                          "values ('OV-99994', 'confirmada', 'v', 'panel', now(), 'v')",
                 constraint="ov_ordenes_transicion_chk"),
    ]
    s, p = c.cancelar(oid)
    c.q(s, p)
    r.append(c.espera("23514", "update ops.ov_ordenes set estado = 'borrador', cancelada_at = null, cancelada_por = null, "
                               "cancelada_origen = null, cancelada_motivo = null where id = %s", (oid,), "ov_ordenes_transicion_chk"))
    sku = "ZZPRUEBA-T2"
    oid2, _ = c.ov_confirmada(sku, 1, fisico=1)
    lid = c.lineas(oid2)[0][0]
    s, p = c.entregar(oid2, [(lid, 1)])
    c.q(s, p)
    r.append(c.espera("23514", "update ops.ov_ordenes set estado = 'confirmada', entregada_at = null, entregada_por = null "
                               "where id = %s", (oid2,), "ov_ordenes_transicion_chk"))
    # Con la marca «¿salió?» no se llega a 'entregada'.
    sku3 = "ZZPRUEBA-T3"
    oid3, _ = c.ov_confirmada(sku3, 1, fisico=1)
    c.q("update ops.ov_ordenes set canal_cancelo_at = now(), canal_cancelo_ref = '4' where id = %s", (oid3,))
    lid3 = c.lineas(oid3)[0][0]
    s, p = c.entregar(oid3, [(lid3, 1)])
    r.append(c.espera("23514", s, p, constraint="ov_ordenes_canal_cancelo_chk"))
    # Formato: nace por_confirmar; descartado es terminal.
    r.append(c.espera("23514", "insert into ops.stock_formato (almacen, estado, archivo_nombre, archivo_hash, cargado_por, "
                               "confirmado_at, confirmado_por, confirmo_bodega) values ('ENSAYO', 'confirmado', 'x.xlsx', %s, 'v', "
                               "now(), 'v', 'b')", ("1" * 64,), "stock_formato_transicion_chk"))
    fid, _, _ = c.uno(SQL_FORMATO_CARGA, {"alm": "ENSAYO", "hash": "2" * 64, "q": QUIEN,
                                          "lineas": json.dumps([{"sku": "ZZPRUEBA-T4", "n": 1, "ubic": None, "tex2": None}])})
    r.append(c.espera("23514", "update ops.stock_formato set estado = 'descartado', descartado_at = now(), descartado_por = 'v' "
                               "where id = %s", (fid,), "stock_formato_desc_chk"))
    c.q("update ops.stock_formato set estado = 'descartado', descartado_at = now(), descartado_por = 'v', "
        "descartado_motivo = 'reemplazado por otro' where id = %s", (fid,))
    r.append(c.espera("42501", "update ops.stock_formato set estado = 'por_confirmar', descartado_at = null, descartado_por = null "
                               "where id = %s", (fid,), "stock_formato_inmutable"))
    # El mismo archivo, ya descartado el anterior, sí vuelve a entrar.
    c.q(SQL_FORMATO_CARGA, {"alm": "ENSAYO", "hash": "2" * 64, "q": QUIEN,
                            "lineas": json.dumps([{"sku": "ZZPRUEBA-T4", "n": 1, "ubic": None, "tex2": None}])})
    fid2 = c.valor("select max(id) from ops.stock_formato")
    r.append(c.espera("23514", "update ops.stock_formato set estado = 'confirmado', confirmado_at = now(), confirmado_por = 'v' "
                               "where id = %s", (fid2,), "stock_formato_conf_chk"))
    r.append(c.espera("KB001", SQL_FORMATO_CONFIRMAR, {"id": c.uno(SQL_FORMATO_CARGA, {
        "alm": "ENSAYO", "hash": "3" * 64, "q": QUIEN, "lineas": "[]"})[0], "rev": 1, "q": QUIEN, "bodega": "B", "tex2": "{}"},
        mensaje="formato_sin_renglones"))
    return f"{len(r)} transiciones o estados rechazados"


@prueba("negativo", "saldo_distinto_del_libro_al_commit")
def _(c: Ctx):
    sku = "ZZPRUEBA-L1"
    c.entrada(sku, 10)
    r = [c.espera("23514", [("update ops.stock_almacen set fisico = fisico + 5 where sku = %s", (sku,))],
                  constraint="stock_libro_cuadra", al_commit=True)]
    # Un movimiento sin tocar el saldo.
    r.append(c.espera("23514", [("""insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, clave, nota, quien)
                                    values (%s, 'ENSAYO', 2, 12, 'correccion', 'corr:prueba-1', 'sin tocar el saldo', 'v')""",
                                 (sku,))], constraint=("stock_libro_cuadra",), al_commit=True))
    # Cadena rota en medio aunque el total cuadre: 10 → (+5 dice 99) → (−2 dice 13).
    r.append(c.espera("23514", [
        ("insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, clave, quien) values "
         "(%s, 'ENSAYO', 5, 99, 'entrada', 'FMT-PRUEBA', 'fmt:990001:' || %s, 'v')", (sku, sku)),
        ("insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, clave, nota, quien) values "
         "(%s, 'ENSAYO', -2, 13, 'correccion', 'corr:prueba-2', 'cadena rota', 'v')", (sku,)),
        ("update ops.stock_almacen set fisico = 13 where sku = %s and almacen = 'ENSAYO'", (sku,))],
        constraint="stock_libro_cadena", al_commit=True))
    # Borrar el saldo con movimientos vivos.
    r.append(c.espera("23514", [("delete from ops.stock_almacen where sku = %s", (sku,))],
                      constraint="stock_libro_cuadra", al_commit=True))
    # Y el modo diferido sigue en pie después de los SAVEPOINT: el descuadre NO
    # truena en su sentencia (fuera de un espera) y sí al forzar el COMMIT.
    c.q("update ops.stock_almacen set fisico = fisico + 1 where sku = %s", (sku,))
    r.append(c.espera("23514", [("select 1", None)], constraint="stock_libro_cuadra", al_commit=True))
    return f"{len(r)} descuadres saldo/libro rechazados al COMMIT (23514), no antes"


@prueba("negativo", "apartado_distinto_de_lo_reservado_al_commit")
def _(c: Ctx):
    sku = "ZZPRUEBA-P1"
    oid, _ = c.ov_confirmada(sku, 2, fisico=10)
    lid = c.lineas(oid)[0][0]
    r = [c.espera("23514", [("update ops.stock_almacen set apartado = apartado + 1 where sku = %s", (sku,))],
                  constraint="stock_apartado_cuadra", al_commit=True)]
    # Soltar el apartado del renglón sin bajar el saldo (H02: restar cantidad en vez de reservado, al revés).
    r.append(c.espera("23514", [("update ops.ov_lineas set reservado = 0 where id = %s", (lid,))],
                      constraint=("stock_apartado_cuadra", "ov_coherente"), al_commit=True))
    # Un borrador que «aparta».
    oid2, _, _ = c.ov([(sku, 3, "ENSAYO")])
    r.append(c.espera("23514", [("update ops.ov_lineas set reservado = cantidad where orden_id = %s", (oid2,)),
                                ("update ops.stock_almacen set apartado = apartado + 3 where sku = %s", (sku,))],
                      constraint=("stock_apartado_cuadra", "ov_coherente"), al_commit=True))
    # Cancelar la OV sin soltar el saldo.
    r.append(c.espera("23514", [("""update ops.ov_ordenes set estado = 'cancelada', cancelada_at = now(), cancelada_por = 'v',
                                    cancelada_origen = 'manual', cancelada_motivo = 'sin soltar' where id = %s""", (oid,))],
                      constraint=("stock_apartado_cuadra", "ov_coherente"), al_commit=True))
    return f"{len(r)} descuadres apartado/Σ reservado rechazados al COMMIT"


@prueba("negativo", "apartado_fuera_de_kubera_o_sin_admite_ov")
def _(c: Ctx):
    r = [
        c.espera("23503", "insert into ops.stock_almacen (sku, almacen, fisico) values ('ZZPRUEBA-O1', 'TEXCO', 5)",
                 constraint="stock_almacen_almacen_fk"),
        c.espera("23514", "insert into ops.stock_almacen (sku, almacen, fisico, apartado) values ('ZZPRUEBA-O1', 'TEXCO', 5, 1)",
                 constraint="stock_almacen_admite_ov_guarda"),
        c.espera("23514", "insert into ops.stock_almacen (sku, almacen, fisico, apartado) values ('ZZPRUEBA-O2', 'TEX3', 5, 1)",
                 constraint="stock_almacen_admite_ov_guarda"),
        c.espera("23514", "insert into ops.stock_almacen (sku, almacen, fisico, apartado) values ('ZZPRUEBA-O3', 'REVISION', 5, 1)",
                 constraint="stock_almacen_admite_ov_guarda"),
        c.espera("23503", """insert into ops.stock_formato (almacen, archivo_nombre, archivo_hash, cargado_por)
                             values ('TEXCO', 'x.xlsx', %s, 'v')""", ("4" * 64,), constraint="stock_formato_almacen_fk"),
        c.espera("23503", """insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, clave, quien)
                             values ('ZZPRUEBA-O5', 'TEX2', 1, 1, 'entrada', 'x', 'fmt:1:ZZPRUEBA-O5', 'v')""",
                 constraint="stock_mov_almacen_fk"),
    ]
    # Un borrador PROPIO (no una OV previa cualquiera, que puede estar cancelada y
    # tronar antes en la guarda con 42501).
    oid, _, _ = c.ov([("ZZPRUEBA-O6", 1, None)])
    r.append(c.espera("23503", "insert into ops.ov_lineas (orden_id, linea, sku, cantidad, almacen) "
                               "values (%s, 2, 'ZZPRUEBA-O4', 1, 'TEX2')", (oid,), constraint="ov_lineas_almacen_fk"))
    r.append(c.espera("23503", "update ops.ov_lineas set almacen = 'TEXCO' where orden_id = %s", (oid,),
                      constraint="ov_lineas_almacen_fk"))
    return f"{len(r)} saldos/renglones/movimientos fuera de kubera o sin admite_ov rechazados"


@prueba("negativo", "libre_negativo_al_subir_apartado")
def _(c: Ctx):
    sku = "ZZPRUEBA-LN"
    c.entrada(sku, 2)
    r = c.espera("23514", "update ops.stock_almacen set apartado = 3 where sku = %s", (sku,),
                 constraint="stock_almacen_libre_guarda")
    oid, _, _ = c.ov([(sku, 3, "ENSAYO")])
    s, p = c.confirmar(oid)
    r2 = c.espera("KB001", s, p, mensaje="no_alcanzo")
    return f"{r}; confirmar 3 con 2 → {r2}"


@prueba("negativo", "checks_del_libro_formato_y_devoluciones")
def _(c: Ctx):
    r = [
        c.espera("23514", "insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, clave, quien) "
                          "values ('ZZPRUEBA-Q', 'ENSAYO', -1, 0, 'entrada', 'x', 'fmt:1:ZZPRUEBA-Q', 'v')",
                 constraint="stock_mov_signo_chk"),
        c.espera("23514", "insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, clave, quien) "
                          "values ('ZZPRUEBA-Q', 'ENSAYO', 1, 1, 'entrada', 'fmt:1:ZZPRUEBA-Q', 'v')",
                 constraint="stock_mov_ref_chk"),
        c.espera("23514", "insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, clave, quien) "
                          "values ('ZZPRUEBA-Q', 'ENSAYO', -1, 0, 'merma', 'merma:1', 'v')", constraint="stock_mov_nota_chk"),
        # La liga con el renglón: obligatoria en devolucion, prohibida en una entrada.
        c.espera("23514", "insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, clave, quien) "
                          "values ('ZZPRUEBA-Q', 'REVISION', 1, 1, 'devolucion', 'DEV-00001', 'dev:1:recibe', 'v')",
                 constraint="stock_mov_ov_chk"),
        c.espera("23514", "insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, clave, ov_linea_id, quien) "
                          "values ('ZZPRUEBA-Q', 'ENSAYO', 1, 1, 'entrada', 'x', 'fmt:1:ZZPRUEBA-Q', 1, 'v')",
                 constraint="stock_mov_ov_chk"),
        c.espera("23514", "insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, clave, quien) "
                          "values ('ZZPRUEBA-Q', 'ENSAYO', 1, 1, 'entrada', 'x', 'cualquier-cosa', 'v')",
                 constraint="stock_mov_clave_chk"),
        c.espera("23514", "insert into ops.stock_formato (almacen, archivo_nombre, archivo_hash, cargado_por) "
                          "values ('ENSAYO', 'x.xlsx', 'NO-ES-HEX', 'v')", constraint="stock_formato_hash_chk"),
        c.espera("23514", "insert into ops.devoluciones (origen, sku, cantidad, paquete_ref, paquete_ids, empaque, recibido_por, clave) "
                          "values ('venta', 'ZZPRUEBA-Q', 1, 'GUIA-1234', '{GUIA1234}', 'cerrado', 'v', 'pantalla:0001')",
                 constraint="devoluciones_venta_chk"),
        c.espera("23514", "insert into ops.devoluciones (origen, sku, cantidad, paquete_ref, paquete_ids, empaque, recibido_por, clave, "
                          "canal, cuenta, nota) values ('retiro_full', 'ZZPRUEBA-Q', 1, 'GUIA-1234', '{guia-1234}', 'cerrado', 'v', "
                          "'pantalla:0002', 'mercado_libre', 'BEKURA', 'retiro de prueba')", constraint="devoluciones_paquete_ids_chk"),
        c.espera("23514", "insert into ops.devoluciones (origen, sku, cantidad, paquete_ref, paquete_ids, empaque, recibido_por, clave, "
                          "canal, cuenta, nota, resuelta_at) values ('retiro_full', 'ZZPRUEBA-Q', 1, 'GUIA-1234', '{GUIA1234}', 'cerrado', "
                          "'v', 'pantalla:0003', 'mercado_libre', 'BEKURA', 'retiro de prueba', now())",
                 constraint="devoluciones_resuelta_chk"),
    ]
    return f"{len(r)} CHECK del libro, del formato y de devoluciones (incluye el NULL de resuelta_chk)"


@prueba("negativo", "almacenes_rastro_y_reglas")
def _(c: Ctx):
    r = [
        c.espera("23514", "update ops.almacenes set surte_ventas = true where codigo = 'TEX3'", constraint="almacenes_motivo_chk"),
        c.espera("23514", "update ops.almacenes set surte_ventas = true, motivo = 'corto' where codigo = 'TEX3'",
                 constraint="almacenes_motivo_chk"),
        c.espera("23514", "update ops.almacenes set surte_ventas = true, motivo = 'Encender TEX3 sin decir quién' "
                          "where codigo = 'TEX3'", constraint="almacenes_quien_chk"),
        c.espera("23514", "update ops.almacenes set cuenta_para_woo = true, motivo = 'Woo sin surtir: prohibido', "
                          "actualizado_por = 'verificador' where codigo = 'TEX3'", constraint="almacenes_woo_chk"),
        c.espera("23514", "update ops.almacenes set admite_ov = true, motivo = 'OV en una bodega de Odoo', "
                          "actualizado_por = 'verificador' where codigo = 'TEXCO'", constraint="almacenes_ov_chk"),
        c.espera("42501", "update ops.almacenes set codigo = 'TEX4' where codigo = 'TEX3'", constraint="almacenes_codigo_fuente_fijos"),
        c.espera("42501", "update ops.almacenes set fuente = 'odoo', odoo_warehouse_id = 999, motivo = 'cambiar la fuente', "
                          "actualizado_por = 'verificador' where codigo = 'TEX3'", constraint="almacenes_codigo_fuente_fijos"),
        c.espera("42501", "delete from ops.almacenes where codigo = 'TEX2'", constraint="almacenes_sin_borrado"),
        # La fase B copiada del plan v3 §10.1 (paso 12): surte sin admitir OV → crear_auto no la vería.
        c.espera("23514", "update ops.almacenes set surte_ventas = true, cuenta_para_woo = true, "
                          "motivo = 'Fase B sin admite_ov (plan v3, paso 12)', actualizado_por = 'verificador' "
                          "where codigo = 'TEX3'", constraint="almacenes_surte_ov_chk"),
        # Alta de una bodega sin acta.
        c.espera("23514", "insert into ops.almacenes (codigo, nombre, fuente, preferencia, surte_ventas, cuenta_para_woo) "
                          "values ('TEX4', 'Nave 4', 'kubera', 4, true, true)", constraint="almacenes_motivo_chk"),
        c.espera("23514", "insert into ops.almacenes (codigo, nombre, fuente, motivo) "
                          "values ('TEX4', 'Nave 4', 'kubera', 'Alta de prueba sin decir quién')", constraint="almacenes_quien_chk"),
    ]
    c.q("update ops.almacenes set nombre = 'Texco 3' where codigo = 'TEX3'")      # el nombre se edita sin acta (D1)
    return f"{len(r)} cambios o altas sin acta o contra las reglas rechazados; el nombre sí se edita"


@prueba("negativo", "sin_truncate")
def _(c: Ctx):
    c.inmediato()       # sin eventos diferidos pendientes: si no, Postgres responde 55006 antes del trigger
    r = []
    # Las que nadie referencia: TRUNCATE directo. (devoluciones se referencia a sí
    # misma por partida_de: Postgres permite el TRUNCATE y llega al trigger.)
    for t in ("stock_almacen", "devoluciones", "stock_formato_linea", "ov_folio", "ov_archivos"):
        r.append(c.espera("42501", f"truncate ops.{t}", constraint=f"{t}_sin_truncate"))
    # Las referenciadas: sin cascade truena la FK (0A000); con cascade, el primer trigger que corre.
    nombres = tuple(f"{t}_sin_truncate" for t in TABLAS) + tuple(f"{t}_solo_agregar" for t in SOLO_AGREGAR)
    for t in ("almacenes", "ov_ordenes", "ov_lineas", "stock_formato"):
        r.append(c.espera("42501", f"truncate ops.{t} cascade", constraint=nombres))
    return f"{len(r)} TRUNCATE rechazados (42501): {', '.join(x.split('/')[1] for x in r[:4])}…"


@prueba("negativo", "cancelada_con_piezas_que_salieron")
def _(c: Ctx):
    sku1, sku2 = "ZZPRUEBA-CS1", "ZZPRUEBA-CS2"
    c.entrada(sku1, 3)
    c.entrada(sku2, 3)
    oid, _, _ = c.ov([(sku1, 1, "ENSAYO"), (sku2, 1, "ENSAYO")])
    s, p = c.confirmar(oid)
    c.q(s, p)
    l1 = c.lineas(oid)[0][0]
    s, p = c.entregar(oid, [(l1, 1)])
    c.q(s, p)
    # Cancelar lo que falta como «cancelada» esconde la pieza que ya salió.
    s, p = c.cancelar(oid, motivo="Cancelada después de una entrega parcial")
    r = [c.espera("23514", [(s, p)], constraint="ov_coherente", al_commit=True)]
    # Un borrador con un renglón «entregado».
    oid2, _, _ = c.ov([("ZZPRUEBA-CS3", 1, "ENSAYO")])
    r.append(c.espera("23514", [("update ops.ov_lineas set entregado = 0, entregado_at = now(), entregado_por = 'v' "
                                 "where orden_id = %s", (oid2,))], constraint="ov_coherente", al_commit=True))
    return f"{len(r)} rechazados al COMMIT: cancelada con salida (→ entregada_cancelada) y borrador con entrega"


@prueba("negativo", "puerta_solo_en_formato_confirmado")
def _(c: Ctx):
    fid, _, _ = c.uno(SQL_FORMATO_CARGA, {"alm": "ENSAYO", "hash": "5" * 64, "q": QUIEN, "lineas": json.dumps(
        [{"sku": "ZZPRUEBA-PZ", "n": 2, "ubic": None, "tex2": None}])})
    r = [
        c.espera("23514", "update ops.stock_formato_linea set salida_odoo_at = now(), salida_odoo_via = 'tex2_cero' "
                          "where formato_id = %s", (fid,), "stock_formato_linea_puerta_chk"),
        c.espera("23514", """insert into ops.stock_formato_linea (formato_id, fila, sku, sku_archivo, cantidad, cantidad_archivo,
                                                               salida_odoo_at, salida_odoo_via)
                             values (%s, 9, 'ZZPRUEBA-PY', 'ZZPRUEBA-PY', 1, 1, now(), 'tex2_cero')""", (fid,),
                 "stock_formato_linea_puerta_chk"),
    ]
    return f"{len(r)}: la puerta no se abre en un formato por confirmar ni nace abierta"


@prueba("negativo", "devoluciones_guarda")
def _(c: Ctx):
    sku = "ZZPRUEBA-DG"
    c.entrada(sku, 3)
    oid, _, _ = c.ov([(sku, 2, "ENSAYO")], mp=("tiktok", "CUENTAPRUEBA", "5770999"), canal="tiktok", cliente="tiktok")
    s, p = c.confirmar(oid)
    c.q(s, p)
    lid = c.lineas(oid)[0][0]
    s, p = c.entregar(oid, [(lid, 2)])
    c.q(s, p)
    alta = """insert into ops.devoluciones (origen, sku, cantidad, ov_linea_id, canal, cuenta, external_order_id,
                                            paquete_ref, paquete_ids, empaque, recibido_por, clave)
              values ('venta', %s, 1, %s, %s, %s, %s, 'GUIA-7788', '{GUIA7788}', 'cerrado', 'v', %s) returning id"""
    did = c.valor(alta, (sku, lid, "tiktok", "CUENTAPRUEBA", "5770999", "pantalla:dg-0001"))   # la misma venta: pasa
    r = [
        c.espera("23514", alta, (sku, lid, "tiktok", "CUENTAPRUEBA", "OTRA-VENTA", "pantalla:dg-0002"),
                 "devoluciones_venta_ov_chk"),
        c.espera("23514", alta, (sku, lid, "temu", None, None, "pantalla:dg-0003"), "devoluciones_venta_ov_chk"),
        c.espera("42501", "update ops.devoluciones set sku = 'ZZPRUEBA-OTRO' where id = %s", (did,), "devoluciones_inmutable"),
        c.espera("42501", "delete from ops.devoluciones where id = %s", (did,), "devoluciones_sin_borrado"),
    ]
    # Antes de resolver, el dictamen y la cantidad sí cambian; resuelta, ya no.
    c.q("update ops.devoluciones set dictamen = 'reparar', dictamen_at = now(), dictamen_por = 'v' where id = %s", (did,))
    c.q("update ops.devoluciones set dictamen = 'merma', dictamen_at = now(), resuelta_at = now(), "
        "nota = 'Pieza rota al revisar' where id = %s", (did,))
    r.append(c.espera("42501", "update ops.devoluciones set nota = 'Otra nota después de resolver' where id = %s", (did,),
                      "devoluciones_inmutable"))
    return f"{len(r)} rechazados: venta ajena a la OV, SKU fijo, sin DELETE, resuelta terminal; reparar → merma sí pasa"


@prueba("negativo", "devuelto_de_mas_al_commit")
def _(c: Ctx):
    sku = "ZZPRUEBA-DM"
    oid, _ = c.ov_confirmada(sku, 1, fisico=1)
    lid = c.lineas(oid)[0][0]
    s, p = c.entregar(oid, [(lid, 1)])
    c.q(s, p)
    # Dos piezas «devueltas» de un renglón que entregó una, con el libro cuadrado:
    # lo que dos recepciones simultáneas dejarían. La sentencia no truena; el COMMIT sí.
    mov = "insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, clave, ov_linea_id, quien) " \
          "values (%s, 'REVISION', %s, %s, 'devolucion', 'DEV-PRUEBA', %s, %s, 'v')"
    r = c.espera("23514", [("insert into ops.stock_almacen (sku, almacen, fisico) values (%s, 'REVISION', 2)", (sku,)),
                           (mov, (sku, 1, 1, "dev:990001:recibe", lid)),
                           (mov, (sku, 1, 2, "dev:990002:recibe", lid))],
                 constraint="stock_devolucion_de_mas", al_commit=True)
    # Una sola pieza sí cuadra.
    c.q("insert into ops.stock_almacen (sku, almacen, fisico) values (%s, 'REVISION', 1)", (sku,))
    c.q(mov, (sku, 1, 1, "dev:990003:recibe", lid))
    c.inmediato()
    return f"devuelto 2 > entregado 1 → {r} al COMMIT; 1 de 1 pasa"


@prueba("negativo", "ov_cerrada_no_reescribe_lo_anotado")
def _(c: Ctx):
    sku = "ZZPRUEBA-RW"
    oid, _ = c.ov_confirmada(sku, 1, fisico=2)
    lid = c.lineas(oid)[0][0]
    s, p = c.entregar(oid, [(lid, 1)])
    c.q(s, p)
    r = [
        c.espera("42501", "update ops.ov_ordenes set entregada_por = 'otro@kubera.mx', rev = rev + 1 where id = %s", (oid,),
                 "ov_ordenes_inmutable"),
        c.espera("42501", "update ops.ov_ordenes set rev = 1 where id = %s", (oid,), "ov_ordenes_inmutable"),
        # entregada → entregada_cancelada CONSERVA entregada_at (v2 §4.1).
        c.espera("42501", "update ops.ov_ordenes set estado = 'entregada_cancelada', cancelada_at = now(), "
                          "cancelada_por = 'automatico', cancelada_origen = 'marketplace', "
                          "cancelada_motivo = 'Cancelada después de entregar', entregada_at = now() + interval '1 hour' "
                          "where id = %s", (oid,), "ov_ordenes_inmutable"),
    ]
    c.q("update ops.ov_ordenes set estado = 'entregada_cancelada', cancelada_at = now(), cancelada_por = 'automatico', "
        "cancelada_origen = 'marketplace', cancelada_motivo = 'Cancelada después de entregar', "
        "devolucion_estado = 'pendiente', rev = rev + 1 where id = %s", (oid,))
    r.append(c.espera("42501", "update ops.ov_ordenes set cancelada_motivo = 'Cliente desistió', "
                               "cancelada_por = 'x@kubera.mx', rev = rev + 1 where id = %s", (oid,), "ov_ordenes_inmutable"))
    c.q("update ops.ov_ordenes set devolucion_estado = 'recibida', rev = rev + 1 where id = %s", (oid,))
    r.append(c.espera("23514", "update ops.ov_ordenes set devolucion_estado = 'pendiente' where id = %s", (oid,),
                      "ov_ordenes_transicion_chk"))
    r.append(c.espera("23514", "update ops.ov_ordenes set devolucion_estado = null where id = %s", (oid,),
                      "ov_ordenes_transicion_chk"))
    # Cerrada se reabre si llega otra pieza (decisión 16).
    c.q("update ops.ov_ordenes set devolucion_estado = 'cerrada', rev = rev + 1 where id = %s", (oid,))
    c.q("update ops.ov_ordenes set devolucion_estado = 'recibida', rev = rev + 1 where id = %s", (oid,))
    c.inmediato()
    return f"{len(r)} reescrituras rechazadas (entrega, cancelación, rev baja, devolución hacia atrás); cerrada se reabre"


@prueba("negativo", "renglones_de_borrada_y_reapartar_despues")
def _(c: Ctx):
    oid, _, _ = c.ov([("ZZPRUEBA-BR", 1, None)])
    c.q("update ops.ov_ordenes set borrada_at = now(), borrada_por = 'admin@prueba', "
        "borrada_motivo = 'Borrador de prueba que sobra', rev = rev + 1 where id = %s", (oid,))
    r = [
        c.espera("42501", "delete from ops.ov_lineas where orden_id = %s", (oid,), "ov_lineas_inmutable"),
        c.espera("42501", "update ops.ov_lineas set cantidad = 3 where orden_id = %s", (oid,), "ov_lineas_inmutable"),
        c.espera("42501", "insert into ops.ov_lineas (orden_id, linea, sku, cantidad) values (%s, 2, 'ZZPRUEBA-BR2', 1)",
                 (oid,), "ov_lineas_inmutable"),
    ]
    # Una OV confirmada hace dos días: soltar y volver a apartar su renglón ya no es «confirmar».
    sku = "ZZPRUEBA-RA"
    c.entrada(sku, 5)
    vieja = c.valor("""insert into ops.ov_ordenes (folio, estado, creado_por, creado_via, confirmada_at, confirmada_por)
                       values ('OV-99993', 'confirmada', 'automatico', 'automatico', now() - interval '2 day', 'automatico')
                       returning id""")
    c.q("insert into ops.ov_lineas (orden_id, linea, sku, cantidad, almacen, reservado) values (%s, 1, %s, 2, 'ENSAYO', 2)",
        (vieja, sku))
    c.q("update ops.stock_almacen set apartado = apartado + 2 where sku = %s and almacen = 'ENSAYO'", (sku,))
    c.inmediato()                                   # la fijación cuadra al COMMIT…
    c.q("set constraints all deferred")             # …y se vuelve a diferir: soltar el apartado es un paso intermedio
    lid = c.lineas(vieja)[0][0]
    r.append(c.espera("42501", [("update ops.ov_lineas set reservado = 0 where id = %s", (lid,)),
                                ("update ops.ov_lineas set reservado = cantidad where id = %s", (lid,))],
                      constraint="ov_lineas_inmutable"))
    return f"{len(r)} rechazados: renglones de una OV borrada y re-apartar fuera de la transacción que confirmó"


@prueba("negativo", "ov_archivos_borrado_logico")
def _(c: Ctx):
    oid, _, _ = c.ov([("ZZPRUEBA-AR", 1, None)])
    aid = c.valor("""insert into ops.ov_archivos (orden_id, tipo, nombre, ruta, sha256, bytes, subido_por)
                     values (%s, 'comprobante', 'c.pdf', 'OV-PRUEBA/c.pdf', %s, 100, 'v') returning id""", (oid, "a" * 64))
    r = [
        c.espera("42501", "delete from ops.ov_archivos where id = %s", (aid,), "ov_archivos_sin_borrado"),
        c.espera("42501", "update ops.ov_archivos set ruta = 'OV-PRUEBA/otro.pdf' where id = %s", (aid,), "ov_archivos_inmutable"),
        c.espera("42501", "update ops.ov_archivos set sha256 = %s where id = %s", ("b" * 64, aid), "ov_archivos_inmutable"),
    ]
    c.q("update ops.ov_archivos set borrado_at = now(), borrado_por = 'v' where id = %s", (aid,))
    r.append(c.espera("42501", "update ops.ov_archivos set borrado_at = null, borrado_por = null where id = %s", (aid,),
                      "ov_archivos_inmutable"))
    return f"{len(r)} rechazados: sin DELETE, fila fija, no revive; marcar borrado sí pasa"


@prueba("negativo", "ov_folio_solo_sube")
def _(c: Ctx):
    r = [
        c.espera("42501", "delete from ops.ov_folio", constraint="ov_folio_solo_sube"),
        c.espera("42501", "update ops.ov_folio set ultimo = ultimo - 1", constraint="ov_folio_solo_sube"),
    ]
    c.q("update ops.ov_folio set ultimo = ultimo + 1")
    return f"{len(r)} rechazados (borrar, bajar); subir sí pasa"


# ═══════════════════════════════════════════════════════════════════════════
# 5. CONCURRENCIA
# ═══════════════════════════════════════════════════════════════════════════
@prueba("concurrencia", "dos_apartados_simultaneos_gana_uno")
def _(c: Ctx):
    if c.en_transaccion:
        raise Omitida("en --en-transaccion las tablas no están confirmadas: una segunda conexión no las ve. "
                      "Correr en el modo por omisión con --concurrencia-con-commit, ya aplicadas en el sandbox.")
    if not c.con_commit:
        raise Omitida("necesita datos CONFIRMADOS en ENSAYO; pasar --concurrencia-con-commit (deja la historia del SKU de "
                      "prueba en el libro de ENSAYO; saldo y apartado vuelven a 0).")
    sku = f"ZZCONC-{int(time.time())}"
    a, b, mira = conectar(c.dsn), conectar(c.dsn), conectar(c.dsn)
    try:
        # Fijación CONFIRMADA: 5 piezas y dos borradores de 5 cada uno.
        ca = Ctx(a, {}, c.dsn, False, True)
        ca.entrada(sku, 5)
        o1, f1, _ = ca.ov([(sku, 5, "ENSAYO")], clave=f"conc:{sku}:1")
        o2, f2, _ = ca.ov([(sku, 5, "ENSAYO")], clave=f"conc:{sku}:2")
        a.commit()
        res: dict[str, str] = {}
        pids: dict[str, int] = {}

        def confirma(cn, oid, nombre):
            cc = Ctx(cn, {}, c.dsn, False, True)
            try:
                # Supavisor (6543) reescribe application_name: se identifica la
                # espera por el pid de servidor, fijo mientras dure la transacción.
                c0 = cn.cursor()
                c0.execute("select pg_backend_pid()")
                pids[nombre] = c0.fetchone()[0]
                s, p = cc.confirmar(oid)
                cc.q(s, p)
                res[nombre] = "gana"
            except psycopg2.Error as e:
                res[nombre] = f"{e.pgcode}:{e.diag.message_primary}"
                cn.rollback()

        confirma(a, o1, "A")                          # A aparta y NO confirma todavía
        hilo = threading.Thread(target=confirma, args=(b, o2, "B"))
        hilo.start()
        espera = False
        for _ in range(50):                           # B tiene que quedarse esperando el candado de A
            time.sleep(0.1)
            if "B" not in pids:
                continue
            cm = mira.cursor()
            cm.execute("select count(*) from pg_stat_activity where pid = %s and wait_event_type = 'Lock'",
                       (pids["B"],))
            espera = cm.fetchone()[0] >= 1
            mira.rollback()
            if espera:
                break
        a.commit()                                    # A gana
        hilo.join(30)
        b.rollback()
        cm = mira.cursor()
        cm.execute("select fisico, apartado from ops.stock_almacen where sku = %s and almacen = 'ENSAYO'", (sku,))
        saldo = cm.fetchone()
        mira.rollback()
        c.afirma(espera, "B nunca esperó el candado de A")
        c.afirma(res.get("A") == "gana" and res.get("B") == "KB001:no_alcanzo", f"resultados: {res}")
        c.afirma(saldo == (5, 5), f"saldo final: {saldo}")
        return f"A gana, B espera el candado y luego KB001 no_alcanzo; apartado 5 de 5 ({sku}, {f1}/{f2} en ENSAYO)"
    finally:
        for cn in (a, b, mira):
            try:
                cn.rollback()
                cn.close()
            except Exception:  # noqa: BLE001
                pass
        if "o1" in locals():
            _limpiar_concurrencia(c.dsn, sku, (o1, o2))


@prueba("concurrencia", "renglon_nuevo_espera_a_un_confirmar")
def _(c: Ctx):
    """La guarda de ov_lineas bloquea la fila de la OV: un INSERT de renglón desde
    otra conexión (un escritor que no pasa por el CAS de rev) espera al confirmar
    en curso y, cuando éste confirma, ve 'confirmada' → 42501. Sin el candado
    pasaría y dejaría una OV confirmada con un renglón sin apartar."""
    if c.en_transaccion:
        raise Omitida("en --en-transaccion las tablas no están confirmadas: una segunda conexión no las ve. "
                      "Correr en el modo por omisión con --concurrencia-con-commit, ya aplicadas en el sandbox.")
    if not c.con_commit:
        raise Omitida("necesita datos CONFIRMADOS en ENSAYO; pasar --concurrencia-con-commit.")
    sku = f"ZZCONC-L{int(time.time())}"
    a, b, mira = conectar(c.dsn), conectar(c.dsn), conectar(c.dsn)
    o1 = None
    try:
        ca = Ctx(a, {}, c.dsn, False, True)
        ca.entrada(sku, 5)
        o1, f1, _ = ca.ov([(sku, 2, "ENSAYO")], clave=f"conc:{sku}:L")
        a.commit()
        s, p = ca.confirmar(o1)
        ca.q(s, p)                                    # T1 confirmó y NO ha hecho COMMIT
        res: dict[str, str] = {}
        pids: dict[str, int] = {}

        def inserta():
            cb = b.cursor()
            try:
                cb.execute("select pg_backend_pid()")     # ver la nota del pid en la prueba anterior
                pids["B"] = cb.fetchone()[0]
                cb.execute("insert into ops.ov_lineas (orden_id, linea, sku, cantidad) values (%s, 2, %s, 1)",
                           (o1, f"{sku}-B"))
                res["B"] = "paso"
            except psycopg2.Error as e:
                res["B"] = f"{e.pgcode}:{e.diag.constraint_name}"
            finally:
                b.rollback()                          # B nunca confirma nada

        hilo = threading.Thread(target=inserta)
        hilo.start()
        espera = False
        for _ in range(50):
            time.sleep(0.1)
            if "B" not in pids:
                continue
            cm = mira.cursor()
            cm.execute("select count(*) from pg_stat_activity where pid = %s and wait_event_type = 'Lock'",
                       (pids["B"],))
            espera = cm.fetchone()[0] >= 1
            mira.rollback()
            if espera:
                break
        a.commit()                                    # T1 confirma
        hilo.join(30)
        c.afirma(espera, "el INSERT del renglón no esperó al confirmar en curso")
        c.afirma(res.get("B") == "42501:ov_lineas_inmutable", f"resultado del INSERT: {res}")
        return f"el INSERT esperó a T1 y luego 42501 ov_lineas_inmutable ({sku}, {f1} en ENSAYO)"
    finally:
        for cn in (a, b, mira):
            try:
                cn.rollback()
                cn.close()
            except Exception:  # noqa: BLE001
                pass
        if o1 is not None:
            _limpiar_concurrencia(c.dsn, sku, (o1,))


def _limpiar_concurrencia(dsn: str, sku: str, ordenes) -> None:
    """Deja ENSAYO como estaba en lo que cuenta (sin apartado ni físico del SKU
    de prueba): cancela las dos OV y corrige el físico a 0 con su movimiento.
    El libro y el chat son de solo agregar: su historia se queda."""
    cn = conectar(dsn)
    try:
        cl = Ctx(cn, {}, dsn, False, True)
        for oid in ordenes:
            if cl.valor("select estado from ops.ov_ordenes where id = %s", (oid,)) in ("borrador", "confirmada"):
                s, p = cl.cancelar(oid, motivo="Limpieza de la prueba de concurrencia")
                cl.q(s, p)
        cl.q("""
            with x as (
              select sa.sku, sa.almacen, sa.fisico from ops.stock_almacen sa
               where sa.sku = %(sku)s and sa.almacen = 'ENSAYO' and sa.fisico > 0
                 for update
            ), s as (
              update ops.stock_almacen sa set fisico = 0
                from x where sa.sku = x.sku and sa.almacen = x.almacen
              returning sa.sku, sa.almacen, x.fisico as antes
            )
            insert into ops.stock_mov (sku, almacen, delta, saldo_despues, motivo, clave, nota, quien, via)
            select s.sku, s.almacen, -s.antes, 0, 'correccion', 'corr:limpieza:' || s.sku,
                   'Limpieza de la prueba de concurrencia', %(q)s, 'prueba'
              from s""", {"sku": sku, "q": QUIEN})
        cn.commit()
    except psycopg2.Error as e:
        cn.rollback()
        print(f"AVISO: no se pudo limpiar la prueba de concurrencia ({e.pgcode}); queda {sku} en ENSAYO.")
    finally:
        cn.close()


# ═══════════════════════════════════════════════════════════════════════════
# Orquestación
# ═══════════════════════════════════════════════════════════════════════════
def correr(ctx: Ctx, aislar) -> list[tuple[str, str, str, str]]:
    resultados = []
    for grupo, nombre, fn in PRUEBAS:
        with aislar():
            try:
                detalle = fn(ctx) or ""
                resultados.append(("OK", grupo, nombre, str(detalle)))
            except Omitida as e:
                resultados.append(("OMITE", grupo, nombre, str(e)))
            except Falla as e:
                resultados.append(("FALLA", grupo, nombre, str(e)))
            except psycopg2.Error as e:
                resultados.append(("FALLA", grupo, nombre,
                                   f"{e.pgcode} {e.diag.constraint_name or ''} {(e.diag.message_primary or '')[:200]}"))
            except Exception as e:  # noqa: BLE001
                resultados.append(("FALLA", grupo, nombre, f"{type(e).__name__}: {e} @ "
                                   f"{traceback.extract_tb(e.__traceback__)[-1].lineno}"))
    return resultados


class _Savepoint:
    def __init__(self, cur):
        self.cur = cur

    def __call__(self):
        return self

    def __enter__(self):
        self.cur.execute("savepoint prueba")

    def __exit__(self, *exc):
        self.cur.execute("rollback to savepoint prueba")
        self.cur.execute("release savepoint prueba")
        return False


class _Transaccion:
    def __init__(self, cn):
        self.cn = cn

    def __call__(self):
        return self

    def __enter__(self):
        self.cn.rollback()

    def __exit__(self, *exc):
        self.cn.rollback()     # NUNCA commit
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Verifica la 0064 y la 0065 en el SANDBOX, sin dejar rastro.")
    ap.add_argument("--en-transaccion", action="store_true",
                    help="aplica 0064 y 0065 dentro de una transacción, corre las pruebas y hace ROLLBACK")
    ap.add_argument("--concurrencia-con-commit", action="store_true",
                    help="(solo modo por omisión) confirma datos de prueba en ENSAYO para la prueba de concurrencia")
    ap.add_argument("--env", help="ruta de env.staging (solo se lee SUPABASE_DB_URL)")
    args = ap.parse_args()
    if args.en_transaccion and args.concurrencia_con_commit:
        sys.exit("ABORTO: --concurrencia-con-commit no va con --en-transaccion.")

    dsn = leer_dsn(args.env)
    try:                                               # ANTES de conectar: falla cerrado sin tocar el sandbox
        migraciones = {"0064": leer_migracion(ARCHIVOS[0]), "0065": leer_migracion(ARCHIVOS[1])}
    except RuntimeError as e:
        print(f"ABORTO: {e}")
        return 2
    cn = conectar(dsn)
    ctx = Ctx(cn, migraciones, dsn, args.en_transaccion, args.concurrencia_con_commit)
    cur = ctx.cur
    t0 = time.time()
    try:
        if args.en_transaccion:
            cur.execute("select txid_current()")
            tx = cur.fetchone()[0]
            cur.execute("set local statement_timeout = '180s'")
            for vuelta in (1, 2):                      # la segunda vuelta ES la prueba de idempotencia
                for clave in ("0064", "0065"):
                    try:
                        cur.execute(migraciones[clave])
                    except psycopg2.Error as e:
                        print(f"FALLA aplicar {clave} (vuelta {vuelta}): {e.pgcode} {(e.diag.message_primary or '')[:300]}")
                        cn.rollback()
                        return 1
                    # Después de CADA archivo: seguimos en la misma transacción, abierta.
                    cur.execute("select txid_current()")
                    if (cur.fetchone()[0] != tx
                            or cn.info.transaction_status != psycopg2.extensions.TRANSACTION_STATUS_INTRANS):
                        print(f"ABORTO: la transacción de prueba cambió al aplicar {clave} (¿un commit en el archivo?).")
                        cn.rollback()
                        return 2
            print(f"0064 y 0065 aplicadas dos veces dentro de la transacción {tx} (sin commit).")
            resultados = correr(ctx, _Savepoint(cur))
            resultados.insert(0, ("OK", "idempotencia", "aplicar_dos_veces", "0064+0065 ×2 sin error, misma transacción"))
        else:
            cur.execute("select to_regclass('ops.almacenes') is not null and to_regclass('ops.stock_almacen') is not null")
            if not cur.fetchone()[0]:
                cn.rollback()
                print("ABORTO: la 0064/0065 no están aplicadas en el sandbox. Usa --en-transaccion.")
                return 2
            cn.rollback()
            resultados = correr(ctx, _Transaccion(cn))
    finally:
        cn.rollback()                                  # NUNCA commit
        cn.close()

    ancho = max(len(f"{g}/{n}") for _, g, n, _ in resultados)
    for estado, grupo, nombre, detalle in resultados:
        print(f"{estado:5} {f'{grupo}/{nombre}':{ancho}}  {detalle}")
    n_ok = sum(1 for r in resultados if r[0] == "OK")
    n_falla = sum(1 for r in resultados if r[0] == "FALLA")
    n_omite = sum(1 for r in resultados if r[0] == "OMITE")
    print(f"\nRESUMEN: {n_ok} OK · {n_falla} FALLA · {n_omite} OMITIDA · {time.time() - t0:.1f} s · "
          f"{'ROLLBACK de todo' if args.en_transaccion else 'cada prueba con ROLLBACK'}")
    return 1 if n_falla else 0


if __name__ == "__main__":
    sys.exit(main())
