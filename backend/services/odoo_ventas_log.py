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

_COLS = ("canal, cuenta, external_order_id, odoo_order_id, odoo_name, estado, "
         "accion, almacen_id, almacen, cobertura, guia, paqueteria, total, "
         "motivo, creado_at, actualizado_at")


def registrar(canal: str, cuenta: str, order_id: str, resultado: dict[str, Any],
              items: list[dict[str, Any]] | None = None,
              guia: str = "", paqueteria: str = "") -> bool:
    """
    Guarda (o actualiza) el renglón de esta venta y sus líneas.

    `resultado` es lo que devolvió `odoo_ventas.crear_orden`/`cancelar_orden`.
    `items` son las líneas normalizadas de la venta — de ahí salen la imagen y
    las unidades; el stock sale de `resultado["stock_foto"]`.

    LA LLAVE ES (canal, cuenta, external_order_id). La `cuenta` entra porque un
    id de orden solo es único DENTRO de una cuenta: el mismo canal con dos
    tiendas puede repetir el número, y sin ella la segunda venta pisaría a la
    primera. Es la misma llave que ya usa `channel.orders`.
    """
    from services import supabase_db as sdb

    foto = resultado.get("stock_foto") or {}
    try:
        with sdb.get_cursor() as cur:
            cur.execute(
                f"""insert into ops.odoo_sale_orders ({_COLS})
                    values (%(canal)s, %(cuenta)s, %(oid)s, %(odoo_id)s, %(nombre)s,
                            %(estado)s, %(accion)s, %(alm_id)s, %(alm)s, %(cob)s,
                            %(guia)s, %(paq)s, %(total)s, %(motivo)s, now(), now())
                    on conflict (canal, cuenta, external_order_id) do update set
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
                {"canal": canal, "cuenta": cuenta, "oid": str(order_id),
                 "odoo_id": resultado.get("odoo_id"),
                 "nombre": resultado.get("nombre"),
                 "estado": resultado.get("estado"),
                 "accion": resultado.get("accion") or ("error" if not resultado.get("ok") else "?"),
                 "alm_id": resultado.get("almacen_id"), "alm": resultado.get("almacen"),
                 "cob": resultado.get("cobertura"), "guia": guia or "",
                 "paq": paqueteria or "", "total": resultado.get("total"),
                 "motivo": resultado.get("motivo")})

            import json as _json
            for n, it in enumerate(items or [], start=1):
                sku = (it.get("sku") or "").strip()
                if not sku:
                    continue
                # La tabla tiene CHECK (cantidad > 0). Una línea en 0 haría
                # fallar el INSERT, y por la llave foránea de las líneas al
                # encabezado se caería la transacción ENTERA — perdiendo la fila
                # y con ella la foto de stock, que es lo irrecuperable. Se salta
                # la línea rara y se conserva el resto.
                cant = int(it.get("cantidad") or 1)
                if cant <= 0:
                    log.warning("odoo_ventas_log: línea %s de %s/%s con cantidad "
                                "%s — se omite (la tabla exige > 0)",
                                sku, canal, order_id, cant)
                    continue
                # La foto va COMPLETA como jsonb, llaveada por id de almacén.
                # Así sumar o quitar una bodega no obliga a migrar columnas, y
                # renombrarla en Odoo no rompe nada.
                f = foto.get(sku) or {}
                cur.execute(
                    """insert into ops.odoo_sale_order_items
                           (canal, cuenta, external_order_id, linea, sku, titulo,
                            imagen, cantidad, precio_unitario, stock_libre)
                       values (%(canal)s, %(cuenta)s, %(oid)s, %(n)s, %(sku)s,
                               %(tit)s, %(img)s, %(cant)s, %(pu)s, %(libre)s::jsonb)
                       on conflict (canal, cuenta, external_order_id, linea)
                       do update set
                           titulo = excluded.titulo,
                           imagen = coalesce(nullif(excluded.imagen, ''),
                                             ops.odoo_sale_order_items.imagen),
                           cantidad = excluded.cantidad,
                           precio_unitario = excluded.precio_unitario
                       -- stock_libre NO se actualiza JAMÁS. Es la foto del
                       -- instante de la venta; refrescarla con el valor de hoy
                       -- la vuelve inútil.
                    """,
                    {"canal": canal, "cuenta": cuenta, "oid": str(order_id),
                     "n": n, "sku": sku,
                     "tit": (it.get("titulo") or "")[:300] or None,
                     "img": it.get("imagen") or "",
                     "cant": cant,
                     "pu": it.get("precio_unitario"),
                     "libre": _json.dumps(f) if f else None})
        return True
    except Exception as exc:  # noqa: BLE001 — una bitácora no tumba una venta
        log.warning("odoo_ventas_log.registrar(%s, %s): %s", canal, order_id, exc)
        return False


def actualizar_guia(canal: str, cuenta: str, order_id: str, guia: str,
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
                where canal = %(c)s and cuenta = %(cu)s
                  and external_order_id = %(o)s
                  and (coalesce(guia, '') is distinct from %(g)s
                       or coalesce(paqueteria, '') is distinct from %(p)s)""",
            {"c": canal, "cu": cuenta, "o": str(order_id),
             "g": guia or "", "p": paqueteria or ""})
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
        # Lo que alguien TIENE QUE HACER ALGO AL RESPECTO. El criterio no es
        # "salió raro", es "queda trabajo pendiente para una persona":
        #
        #   error / sku_sin_producto  → la orden NO se creó: el almacén no se
        #                               enteró de una venta que sí ocurrió.
        #   no_se_pudo_cancelar       → TikTok canceló pero Odoo se negó (ya
        #                               tiene entrega hecha o factura). La orden
        #                               sigue VIVA para una venta muerta y hay
        #                               que cancelarla a mano.
        #   cobertura parcial         → se creó sin respaldo de inventario: la
        #                               reserva no va a ocurrir y el stock no
        #                               bajará solo. Sobreventa esperando.
        #
        # Quedan FUERA a propósito las que no piden nada: `nacio_cancelada`
        # (la venta llegó muerta, no hay nada que hacer), `apagado`, `simulado`,
        # `ya_existia`, `ya_cancelada` y `sin_orden`.
        donde.append("(o.accion in ('error','sku_sin_producto',"
                     "'no_se_pudo_cancelar') or o.cobertura = 'parcial')")
    try:
        filas = sdb.fetch_all(
            f"""select o.canal, o.cuenta, o.external_order_id, o.odoo_order_id,
                       o.odoo_name, o.estado, o.accion, o.almacen, o.cobertura,
                       o.guia, o.paqueteria, o.total, o.motivo, o.creado_at,
                       -- CUÁNDO COMPRÓ EL CLIENTE, que NO es `creado_at`: ése es
                       -- cuándo lo procesamos nosotros. La cancelación de una
                       -- venta de agosto entra hoy, y con una sola fecha en
                       -- pantalla se lee como una venta de hoy que falló. Pasó
                       -- el 2-sep con la 585572145234216465 (compra del 22-ago).
                       (select c.creado_at from channel.orders c
                         where c.canal = o.canal and c.cuenta = o.cuenta
                           and c.external_order_id = o.external_order_id) as venta_at,
                       coalesce(
                         (select json_agg(json_build_object(
                             'sku', i.sku, 'titulo', i.titulo, 'imagen', i.imagen,
                             'cantidad', i.cantidad, 'precio_unitario', i.precio_unitario,
                             'stock_libre', i.stock_libre)
                             order by i.linea)
                            from ops.odoo_sale_order_items i
                           where i.canal = o.canal
                             and i.cuenta = o.cuenta
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


def resumen(canal: str | None = None) -> dict[str, Any]:
    """
    Los contadores de arriba del tab.

    `canal` los acota a esa pestaña. Sin él salen los de todos los canales
    juntos, que era lo correcto cuando había una sola lista y deja de serlo en
    cuanto la pantalla se parte por canal: un contador que suma TikTok y Temu
    debajo de la pestaña de Temu miente sobre lo que se está mirando.
    """
    from services import supabase_db as sdb
    params: dict[str, Any] = {}
    donde = "creado_at >= now() - interval '30 days'"
    if canal:
        donde += " and canal = %(canal)s"
        params["canal"] = canal
    try:
        filas = sdb.fetch_all(
            f"""select accion, cobertura, count(*) n
                 from ops.odoo_sale_orders
                where {donde}
                group by 1, 2""", params)
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
