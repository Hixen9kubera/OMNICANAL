"""
odoo_ventas_log.py — La bitácora de las órdenes de venta que creamos en Odoo.

Es lo que alimenta la pestaña **Automatización**: qué orden se creó, con qué
guía, con qué SKUs, y —lo único que no se puede reconstruir después— **cuánto
stock había en el instante de la venta**.

POR QUÉ NO SE LE PREGUNTA A ODOO EN VIVO
────────────────────────────────────────
Casi todo lo que pinta el tab existe en Odoo: nombre, estado, líneas. Pero
`free_qty` cambia con cada venta, cada recepción y cada reserva. Veinte minutos
después ya no se puede saber cuánto había cuando el comprador apretó el botón —
y ése es justo el dato con el que se contesta "¿por qué se sobrevendió?".

Por eso la foto se ESCRIBE UNA VEZ y no se re-toca nunca (ver el UPSERT: las
columnas de stock no aparecen en el `DO UPDATE`). Es la misma regla que ya
protege `total` y `comision` en `channel.orders`: un dato histórico deja de
servir en cuanto alguien lo "refresca" con el valor de hoy.

NUNCA ROMPE UNA VENTA
─────────────────────
Todo va dentro de `try`. Si la tabla todavía no existe —la migración 0033 la
aplica Eduardo en producción— esto avisa al log y sigue. Una bitácora que falla
no puede tumbar el registro de una venta.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("omnicanal.odoo_ventas_log")

_COLS = ("canal, external_order_id, odoo_order_id, odoo_name, estado, accion, "
         "almacen_id, almacen, cobertura, guia, paqueteria, total, motivo, "
         "creado_at, actualizado_at")


def registrar(canal: str, order_id: str, resultado: dict[str, Any],
              items: list[dict[str, Any]] | None = None,
              guia: str = "", paqueteria: str = "") -> bool:
    """
    Guarda (o actualiza) el renglón de esta venta y sus líneas.

    `resultado` es lo que devolvió `odoo_ventas.crear_orden`/`cancelar_orden`.
    `items` son las líneas normalizadas de la venta — de ahí salen la imagen y
    las unidades; el stock sale de `resultado["stock_foto"]`.
    """
    from services import supabase_db as sdb

    foto = resultado.get("stock_foto") or {}
    try:
        with sdb.get_cursor() as cur:
            cur.execute(
                f"""insert into ops.odoo_sale_orders ({_COLS})
                    values (%(canal)s, %(oid)s, %(odoo_id)s, %(nombre)s, %(estado)s,
                            %(accion)s, %(alm_id)s, %(alm)s, %(cob)s, %(guia)s,
                            %(paq)s, %(total)s, %(motivo)s, now(), now())
                    on conflict (canal, external_order_id) do update set
                        odoo_order_id = coalesce(excluded.odoo_order_id,
                                                 ops.odoo_sale_orders.odoo_order_id),
                        odoo_name     = coalesce(excluded.odoo_name,
                                                 ops.odoo_sale_orders.odoo_name),
                        estado        = excluded.estado,
                        accion        = excluded.accion,
                        -- La guía puede llegar VACÍA en el primer aviso y con
                        -- valor después; nunca al revés. `nullif`+`coalesce`
                        -- deja pasar '' → valor y bloquea valor → ''.
                        guia          = coalesce(nullif(excluded.guia, ''),
                                                 ops.odoo_sale_orders.guia),
                        paqueteria    = coalesce(nullif(excluded.paqueteria, ''),
                                                 ops.odoo_sale_orders.paqueteria),
                        motivo        = excluded.motivo,
                        actualizado_at = now()
                    -- almacen/cobertura/total NO se re-tocan: son de la decisión
                    -- original y describen el momento en que se creó la orden.
                """,
                {"canal": canal, "oid": str(order_id),
                 "odoo_id": resultado.get("odoo_id"),
                 "nombre": resultado.get("nombre"),
                 "estado": resultado.get("estado"),
                 "accion": resultado.get("accion") or ("error" if not resultado.get("ok") else "?"),
                 "alm_id": resultado.get("almacen_id"), "alm": resultado.get("almacen"),
                 "cob": resultado.get("cobertura"), "guia": guia or "",
                 "paq": paqueteria or "", "total": resultado.get("total"),
                 "motivo": resultado.get("motivo")})

            for n, it in enumerate(items or [], start=1):
                sku = (it.get("sku") or "").strip()
                if not sku:
                    continue
                f = foto.get(sku) or {}
                cur.execute(
                    """insert into ops.odoo_sale_order_items
                           (canal, external_order_id, linea, sku, titulo, imagen,
                            cantidad, precio_unitario, stock_texco, stock_texco2)
                       values (%(canal)s, %(oid)s, %(n)s, %(sku)s, %(tit)s, %(img)s,
                               %(cant)s, %(pu)s, %(s1)s, %(s2)s)
                       on conflict (canal, external_order_id, linea) do update set
                           titulo = excluded.titulo,
                           imagen = coalesce(nullif(excluded.imagen, ''),
                                             ops.odoo_sale_order_items.imagen),
                           cantidad = excluded.cantidad,
                           precio_unitario = excluded.precio_unitario
                       -- stock_texco / stock_texco2 NO se actualizan JAMÁS.
                       -- Son la foto del instante de la venta; refrescarlas con
                       -- el valor de hoy las vuelve inútiles.
                    """,
                    {"canal": canal, "oid": str(order_id), "n": n, "sku": sku,
                     "tit": (it.get("titulo") or "")[:300] or None,
                     "img": it.get("imagen") or "",
                     "cant": int(it.get("cantidad") or 1),
                     "pu": it.get("precio_unitario"),
                     "s1": f.get("TEXCO"), "s2": f.get("TEXCO II")})
        return True
    except Exception as exc:  # noqa: BLE001 — una bitácora no tumba una venta
        log.warning("odoo_ventas_log.registrar(%s, %s): %s", canal, order_id, exc)
        return False


def actualizar_guia(canal: str, order_id: str, guia: str,
                    paqueteria: str = "") -> bool:
    """
    Refresca SOLO la guía y la paquetería de una venta ya registrada.

    POR QUÉ HACE FALTA. `registrar` corre en la PRIMERA vista de la venta —
    cuando nace la orden en Odoo— y no vuelve a correr en los avisos
    siguientes. TikTok suele mandar ya el `tracking_number` desde el primer
    evento, pero no siempre: si la etiqueta se genera después, o si se cancela
    el envío y se rehace, la guía llega en un aviso POSTERIOR. Sin esto, la
    columna se quedaba vacía para siempre y el tab no servía justo para lo que
    lo abre quien empaca.

    Es un UPDATE, no un upsert, a propósito: si la venta no está registrada no
    hay que inventarle una fila a medias. Y no pisa una guía existente con
    vacío — solo avanza de '' a valor.
    """
    from services import supabase_db as sdb

    if not (guia or paqueteria):
        return False
    try:
        n = sdb.execute(
            """update ops.odoo_sale_orders
                  set guia = coalesce(nullif(%(g)s, ''), guia),
                      paqueteria = coalesce(nullif(%(p)s, ''), paqueteria),
                      actualizado_at = now()
                where canal = %(c)s and external_order_id = %(o)s
                  and (coalesce(guia, '') is distinct from %(g)s
                       or coalesce(paqueteria, '') is distinct from %(p)s)""",
            {"c": canal, "o": str(order_id), "g": guia or "", "p": paqueteria or ""})
        return bool(n)
    except Exception as exc:  # noqa: BLE001
        log.debug("odoo_ventas_log.actualizar_guia(%s, %s): %s", canal, order_id, exc)
        return False


def historial(limite: int = 100, canal: str | None = None,
              solo_problemas: bool = False) -> list[dict[str, Any]]:
    """Lo que pinta el tab: una fila por venta, con sus líneas anidadas."""
    from services import supabase_db as sdb

    donde, params = ["1=1"], {"lim": int(limite)}
    if canal:
        donde.append("o.canal = %(canal)s")
        params["canal"] = canal
    if solo_problemas:
        # Lo que alguien tiene que MIRAR: no se creó, o se creó sin respaldo de
        # inventario (que es sobreventa esperando a ocurrir).
        donde.append("(o.accion in ('error','sku_sin_producto') "
                     "or o.cobertura = 'parcial')")
    try:
        filas = sdb.fetch_all(
            f"""select o.canal, o.external_order_id, o.odoo_order_id, o.odoo_name,
                       o.estado, o.accion, o.almacen, o.cobertura, o.guia,
                       o.paqueteria, o.total, o.motivo, o.creado_at,
                       coalesce(
                         (select json_agg(json_build_object(
                             'sku', i.sku, 'titulo', i.titulo, 'imagen', i.imagen,
                             'cantidad', i.cantidad, 'precio_unitario', i.precio_unitario,
                             'stock_texco', i.stock_texco, 'stock_texco2', i.stock_texco2)
                             order by i.linea)
                            from ops.odoo_sale_order_items i
                           where i.canal = o.canal
                             and i.external_order_id = o.external_order_id),
                         '[]'::json) as lineas
                  from ops.odoo_sale_orders o
                 where {' and '.join(donde)}
                 order by o.creado_at desc
                 limit %(lim)s""", params)
        return [dict(f) for f in filas]
    except Exception as exc:  # noqa: BLE001
        log.warning("odoo_ventas_log.historial: %s", exc)
        return []


def resumen() -> dict[str, Any]:
    """Los contadores de arriba del tab."""
    from services import supabase_db as sdb
    try:
        filas = sdb.fetch_all(
            """select accion, cobertura, count(*) n
                 from ops.odoo_sale_orders
                where creado_at >= now() - interval '30 days'
                group by 1, 2""")
        total = sum(f["n"] for f in filas)
        # El GROUP BY es por (accion, cobertura), así que una misma acción
        # aparece en VARIAS filas — una por cobertura. Construir el dict por
        # comprensión dejaba solo la última y el contador salía más bajo que la
        # realidad. Se ACUMULA.
        por_accion: dict[str, int] = {}
        for f in filas:
            por_accion[f["accion"]] = por_accion.get(f["accion"], 0) + f["n"]
        return {
            "total_30d": total,
            "por_accion": por_accion,
            "parciales": sum(f["n"] for f in filas if f["cobertura"] == "parcial"),
            "errores": sum(f["n"] for f in filas
                           if f["accion"] in ("error", "sku_sin_producto")),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("odoo_ventas_log.resumen: %s", exc)
        return {"total_30d": 0, "por_accion": {}, "parciales": 0, "errores": 0,
                "nota": "la tabla ops.odoo_sale_orders todavía no existe"}
