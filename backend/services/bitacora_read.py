"""
bitacora_read.py — Lecturas de la bitácora de creación desde `ops.process_log`
(BD kubera). Gemelas de las consultas sobre `crear_logs` de routers/crear.py.

Traducción crear_logs → ops.process_log:
    sku      → sku            estado  → estado
    paso     → accion         creado  → created_at
    detalle  → detalle (jsonb)
    wc_id    → detalle->>'wc_id'   ← NO hay columna: vive dentro del detalle
                                     (rellenado por backfill_crear_logs.py)

DOS DIFERENCIAS QUE NO SON COSMÉTICAS:

1. **El orden es por FECHA, no por id.** Las consultas de MySQL buscaban el
   último evento de cada SKU con `MAX(id)`. En kubera el id es una secuencia y
   el backfill del 12-ago cargó la semana del 15 al 23 de julio DESPUÉS de todo
   agosto: esas filas de julio tienen los ids más altos de la tabla. Ordenar
   por id mostraría julio como "lo último" en el historial de esos SKUs.

2. **`detalle` puede ser NULO.** El espejo lo guardaba así cuando el único
   campo era el wc_id que él mismo excluía. Todo acceso va con `coalesce`: en
   jsonb, cualquier operador sobre NULL devuelve NULL y se propaga en silencio.
"""
from __future__ import annotations

from typing import Any

from services import supabase_db as sdb

# Una fila por SKU con su ÚLTIMO evento. `distinct on` es la forma idiomática
# en Postgres del `JOIN (SELECT MAX(id) … GROUP BY sku)` de MySQL, y de paso
# permite desempatar por fecha primero e id después.
_ULTIMO = """
    select distinct on (l.sku)
           l.sku::text as sku, l.estado, l.accion as paso,
           l.detalle, l.created_at as creado,
           (l.detalle ->> 'wc_id') as wc_id
      from ops.process_log l
     where l.proceso = 'crear'
       and l.created_at >= now() - make_interval(days => %(dias)s)
       {filtro_sku}
     order by l.sku, l.created_at desc, l.id desc
"""


def _fila(r: dict) -> dict[str, Any]:
    """Forma idéntica a la del par MySQL, con wc_id fuera del detalle.

    EL MENSAJE DE UN FALLO YA NO VIVE EN `accion`. Desde el 4-sep-2026,
    `crear_producto._accion_y_mensaje` guarda `accion='error'` y manda el texto a
    `detalle.mensaje` — antes el mensaje entero iba dentro de la columna de la
    acción, así que cada error distinto era una "acción" distinta y no se podía
    contar cuántas creaciones fallaron.

    Aquí se deshace para quien lee: el `paso` que ve la pantalla de auditoría de
    Crear vuelve a ser el mensaje cuando lo hay. Las filas ANTERIORES no tienen
    `detalle.mensaje` y siguen saliendo por `accion`, así que las dos formas
    conviven sin que nadie note el corte.
    """
    det = dict(r.get("detalle") or {})
    det.pop("wc_id", None)
    wc = r.get("wc_id")
    mensaje = det.pop("mensaje", None)
    return {"sku": r["sku"], "wc_id": int(wc) if wc else None,
            "estado": r.get("estado"), "paso": mensaje or r.get("paso"),
            "detalle": det or None, "creado": r.get("creado")}


def historial(page: int, per_page: int, sku: str | None,
              estado: str | None, dias: int) -> tuple[list[dict], int]:
    """(items, total) — una fila por SKU con su último evento, paginado."""
    args: dict[str, Any] = {"dias": dias}
    filtro = ""
    if sku:
        filtro = "and l.sku::text ilike %(sku)s"
        args["sku"] = f"%{sku}%"
    base = _ULTIMO.format(filtro_sku=filtro)
    # El estado se filtra SOBRE el último evento, no dentro de la ventana: así
    # lo hacía el par MySQL y cambiarlo alteraría lo que ve el panel.
    where_estado = "where u.estado = %(estado)s" if estado else ""
    if estado:
        args["estado"] = estado
    total = sdb.fetch_scalar(
        f"select count(*) from ({base}) u {where_estado}", args) or 0
    args["limite"] = per_page
    args["salto"] = (page - 1) * per_page
    rows = sdb.fetch_all(
        f"""select * from ({base}) u {where_estado}
             order by u.creado desc limit %(limite)s offset %(salto)s""", args)
    return [_fila(r) for r in rows], int(total)


def historial_sku(sku: str, limite: int) -> list[dict]:
    """Todos los eventos de UN SKU, más recientes primero."""
    return [_fila(r) for r in sdb.fetch_all(
        """select l.sku::text as sku, l.estado, l.accion as paso,
                  l.detalle, l.created_at as creado,
                  (l.detalle ->> 'wc_id') as wc_id
             from ops.process_log l
            where l.proceso = 'crear' and l.sku = %(s)s::citext
            order by l.created_at desc, l.id desc limit %(n)s""",
        {"s": sku, "n": limite})]


def completados(dias: int) -> list[dict]:
    """
    Último evento COMPLETADO de cada SKU en la ventana — el insumo de
    /auditoria, que le pregunta a WooCommerce si esos productos siguen vivos.

    Ojo con el matiz: se filtra a `completado` ANTES de tomar el último, no
    después. Es lo que hacía el par MySQL, y no es lo mismo: un SKU que se
    completó y más tarde registró un error se sigue auditando —el producto se
    creó de verdad y puede haber desaparecido de Woo—, mientras que "el último
    evento, si fue completado" lo dejaría fuera justo cuando más importa.
    """
    rows = sdb.fetch_all(
        """select distinct on (l.sku)
                  l.sku::text as sku, l.estado, l.accion as paso,
                  l.detalle, l.created_at as creado,
                  (l.detalle ->> 'wc_id') as wc_id
             from ops.process_log l
            where l.proceso = 'crear' and l.estado = 'completado'
              and l.created_at >= now() - make_interval(days => %(dias)s)
            order by l.sku, l.created_at desc, l.id desc""", {"dias": dias})
    return [_fila(r) for r in rows]
