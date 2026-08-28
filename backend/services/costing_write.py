"""
costing_write.py — CORTE F6 del dominio COSTOS (opción A: espejo inverso).

Con SUPABASE_WRITE_COSTING=true la BD kubera pasa a ser la FUENTE DE VERDAD de
las escrituras de costos (costos_finales, costos_validados y la bitácora):

  1. La escritura PRIMARIA va a costing.* / ops.process_log, SÍNCRONA y en la
     misma petición, con la atribución que lee el trigger de cost_history
     (reutiliza los upserts a nivel cursor de costing_mirror — el mismo SQL
     que validó la racha 14/14 del acta).
  2. MySQL se vuelve el ESPEJO INVERSO: se escribe DESPUÉS, en hilo y
     best-effort — un fallo de MySQL jamás rompe la operación (log +
     ops.migration_issues + Slack). Mientras dure la transición, el fallback
     de lectura F5 sigue teniendo datos frescos y el acta diaria
     (deltas-costos) sigue debiendo dar cero.
  3. RESILIENCIA (kubera caída): el negocio NO se bloquea — se escribe MySQL
     como en el mundo viejo (el dato no se pierde) y el evento kubera queda
     ENCOLADO en espejo_kubera_log con payload reproducible; se re-aplica con
     POST /api/migracion/errores/reprocesar (handlers costing.* en
     kubera_mirror). Slack avisa por la vía normal del espejo.
  4. Revertir = SUPABASE_WRITE_COSTING=false → vuelve el dual-write clásico
     (MySQL manda, costing_mirror espeja). Cero deploys.

El llamador (costos.py) pasa la escritura MySQL como thunk: este módulo no
duplica el SQL de MySQL, solo decide el orden y la resiliencia.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from config import settings
from core import actor
from services import costing_mirror, supabase_db as sdb

log = logging.getLogger("omnicanal.costing_write")

_ARCHIVO = "services/costos.py"


def activo() -> bool:
    return settings.supabase_write_costing and sdb.disponible()


def _en_hilo(fn: Callable, *args) -> None:
    # Delega en core.actor: `run_in_executor` NO se lleva los contextvars, y por
    # aquí pasan las escrituras de costos — justo las que hay que atribuir.
    actor.en_hilo(fn, *args)


def _espejo_inverso_mysql(tabla: str, sku: str, escribir_mysql: Callable[[], None]) -> None:
    """El espejo inverso: MySQL después de kubera, best-effort."""
    try:
        escribir_mysql()
    except Exception as exc:  # noqa: BLE001
        log.warning("espejo inverso MySQL %s(%s) falló (la operación continúa): %s",
                    tabla, sku, exc)
        # kubera está arriba (la primaria acaba de pasar): el issue sí llega.
        costing_mirror._registrar_issue(tabla, sku, f"espejo inverso MySQL fallo: {exc}")
        try:
            from services import alertas
            alertas.avisar(
                "espejo_inverso:costing",
                f"*Espejo inverso de COSTOS a MySQL falló* (`{tabla}`, {sku}): "
                f"{type(exc).__name__}: {str(exc)[:140]}. kubera SÍ guardó; "
                f"MySQL quedará desfasado hasta el acta/reproceso.",
            )
        except Exception:  # noqa: BLE001
            pass


def _encolar_kubera(funcion: str, tabla_mysql: str, tabla_kubera: str, sku: str,
                    payload: dict[str, Any], exc: Exception) -> None:
    """kubera caída: el evento queda en espejo_kubera_log (MySQL, local) con
    payload reproducible por los handlers costing.* de reprocesar_errores.
    _persistir_error también dispara la alerta de Slack."""
    try:
        from services import kubera_mirror
        kubera_mirror._persistir_error(
            _ARCHIVO, funcion, tabla_mysql, tabla_kubera, "UPSERT", sku, exc, payload)
    except Exception as exc2:  # noqa: BLE001
        log.error("no se pudo encolar el evento kubera %s(%s): %s", tabla_kubera, sku, exc2)


def _escribir(funcion: str, tabla_mysql: str, tabla_kubera: str, sku: str,
              payload: dict[str, Any], primaria: Callable[[], None],
              escribir_mysql: Callable[[], None]) -> None:
    """Orden del corte: kubera primero (síncrona); MySQL como espejo en hilo.
    Si kubera falla: MySQL aguanta el negocio + evento encolado."""
    try:
        primaria()
    except Exception as exc:  # noqa: BLE001
        log.warning("escritura primaria kubera %s(%s) falló — MySQL aguanta y el "
                    "evento se encola: %s", tabla_kubera, sku, exc)
        escribir_mysql()  # si esto también truena, el error SÍ sube al llamador
        _encolar_kubera(funcion, tabla_mysql, tabla_kubera, sku, payload, exc)
        return
    # Desmantelamiento (paso 1): sin espejo inverso las tablas MySQL de costos
    # quedan congeladas a propósito. kubera ya guardó. El camino de emergencia
    # de arriba (kubera caída → MySQL absorbe + cola) NO depende de este flag.
    if not settings.costing_espejo_inverso:
        return
    _en_hilo(_espejo_inverso_mysql, tabla_mysql, sku, escribir_mysql)


def guardar_validados(sku: str, fila: dict[str, Any],
                      escribir_mysql: Callable[[], None],
                      accion: str = "auto", origen: str = "backend") -> None:
    def _primaria() -> None:
        with sdb.get_cursor() as cur:
            costing_mirror._atribuir(cur, accion, origen)
            costing_mirror._asegurar_identidad(cur, sku)
            costing_mirror.upsert_validados(cur, sku, fila)

    _escribir("guardar_validados", "costos_validados", "costing.costos_validados",
              sku, {**fila, "sku": sku, "accion": accion, "origen": origen},
              _primaria, escribir_mysql)


def guardar_finales(sku: str, fila: dict[str, Any],
                    escribir_mysql: Callable[[], None],
                    accion: str = "auto", origen: str = "backend") -> None:
    def _primaria() -> None:
        with sdb.get_cursor() as cur:
            costing_mirror._atribuir(cur, accion, origen)
            costing_mirror._asegurar_identidad(cur, sku)
            costing_mirror.upsert_finales(cur, sku, fila)

    _escribir("guardar_finales", "costos_finales", "costing.costos_finales",
              sku, {**fila, "sku": sku, "accion": accion, "origen": origen},
              _primaria, escribir_mysql)


def marcar_revisado(sku: str, revisado: bool = True) -> dict[str, Any] | None:
    """
    Pone o quita la marca de "ya revisé este costeo" (migración 0032).

    NO pasa por el dual-write ni por ``_escribir``, a propósito: las columnas
    ``revisado_at``/``revisado_por`` solo existen en kubera. La tabla
    ``costos_validados`` de MySQL quedó congelada con el corte del 13-ago y no
    las tiene, así que espejarlas sería escribir a un lugar que no las entiende.

    Tampoco toca ningún número del costeo — por eso no llama a ``_atribuir``
    ni dispara nada de ``cost_history``: no hay cambio de costo que historiar.
    La firma sale del cable de la v0.233.0 (``app.usuario``, puesto por
    ``supabase_db.get_cursor`` desde ``core/actor.py``), leído aquí mismo en el
    UPDATE. Probado contra producción: persona con Bearer → su correo; máquina
    con X-API-Key → ``servicio``; sin cable (SQL directo, cron) → NULO. Nunca
    "backend", que era la etiqueta que borraba la diferencia.

    Devuelve la fila con la marca, o ``None`` si el SKU no tiene costeo: marcar
    como revisado algo que no existe no es un caso válido, y un silencio ahí
    haría creer al panel que se guardó.
    """
    sql = (
        """update costing.costos_validados
              set revisado_at  = now(),
                  revisado_por = nullif(current_setting('app.usuario', true), '')
            where sku = %s
        returning sku::text as sku, revisado_at, revisado_por"""
        if revisado else
        """update costing.costos_validados
              set revisado_at = null, revisado_por = null
            where sku = %s
        returning sku::text as sku, revisado_at, revisado_por"""
    )
    with sdb.get_cursor() as cur:
        cur.execute(sql, (sku,))
        fila = cur.fetchone()
    if fila is None:
        log.info("marcar_revisado(%s): sin fila en costos_validados", sku)
    return dict(fila) if fila else None


def guardar_caja_compartida(
    sku: str, contenedor: str, archivo: str, renglones: list[int], *,
    skus_grupo: list[str] | None = None,
    piezas_grupo: float | None = None,
    cbm_grupo: float | None = None,
    cbm_origen: str | None = None,
    nota: str | None = None,
) -> dict[str, Any] | None:
    """
    De DÓNDE salió el costo: el archivo y los renglones exactos del packing list.

    Es la procedencia que `costos_validados` no podía guardar. Ahí el costo es
    una cifra sin origen: si mañana no cuadra, no hay forma de volver al renglón
    que la produjo. Aquí queda el rastro para poder auditarla.

    La llave es ``(sku, contenedor_base)``, y ``contenedor_base`` es una columna
    GENERADA que le quita el sufijo de embarque (``MRKU4831449 - 88`` →
    ``MRKU4831449``). Eso resuelve el desencuentro de toda la vida: el packing
    list trae el código pelón y ``costos_validados`` lo guarda sufijado. Por eso
    el mismo embarque capturado de las dos formas cae en la MISMA fila y se
    corrige en vez de duplicarse.

    Y por eso mismo **dos contenedores son dos filas**: el mismo SKU en un
    embarque posterior tiene otro ``contenedor_base``, con su propio archivo,
    sus propios renglones y su propia fecha. Ninguna pisa a la otra — que es
    justo lo que unas columnas en ``costos_validados`` no podían hacer.

    ``renglones`` va como viene del Excel (1-based, los que ve el humano) y la
    tabla exige al menos uno. Cuando el cartón se comparte son TODOS los del
    grupo, no solo el del SKU: el flete se repartió entre esos renglones y sin
    ellos el número no se puede reconstruir.

    No pasa por el dual-write: la tabla solo existe en kubera.
    """
    filas = [int(r) for r in (renglones or []) if r is not None]
    if not filas:
        raise ValueError(f"{sku}: no se puede registrar procedencia sin renglones")
    with sdb.get_cursor() as cur:
        cur.execute(
            """insert into costing.caja_compartida
                   (sku, contenedor, archivo, renglones,
                    skus_grupo, piezas_grupo, cbm_grupo, cbm_origen, nota)
               values (%s, %s, %s, %s, %s, %s, %s,
                       coalesce(%s, 'no_parseado'), %s)
               on conflict (sku, contenedor_base) do update
                  set contenedor   = excluded.contenedor,
                      archivo      = excluded.archivo,
                      renglones    = excluded.renglones,
                      skus_grupo   = excluded.skus_grupo,
                      piezas_grupo = excluded.piezas_grupo,
                      cbm_grupo    = excluded.cbm_grupo,
                      cbm_origen   = excluded.cbm_origen,
                      nota         = excluded.nota,
                      updated_at   = now()
            returning sku::text as sku, contenedor_base, renglones, archivo""",
            (sku, contenedor, archivo, filas,
             skus_grupo or None,
             piezas_grupo, cbm_grupo, cbm_origen, nota))
        fila = cur.fetchone()
    return dict(fila) if fila else None


def registrar_log(sku: str, accion: str, origen: str, detalle: dict[str, Any],
                  escribir_mysql: Callable[[], None]) -> None:
    """Bitácora bajo el corte: ops.process_log primaria, costos_logs espejo.
    El payload de la cola usa la forma de _up_process_log (reprocesable)."""
    def _primaria() -> None:
        with sdb.get_cursor() as cur:
            costing_mirror.insertar_log(cur, sku, accion, origen, detalle)

    _escribir("registrar_log", "costos_logs", "ops.process_log", sku,
              {"proceso": "costos", "origen": origen or "backend", "sku": sku,
               "accion": accion, "estado": "ok", "detalle": detalle},
              _primaria, escribir_mysql)
