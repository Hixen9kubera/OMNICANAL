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

# La venta dejó su ESPACIO y espera la guía para que nazca su orden. Es la
# acción que arma la cola invertida de `odoo_ventas.cola_de_guias`.
ACCION_ESPERA = "espera_guia"
# Se canceló ANTES de tener guía: no hay nada que crear y no debe haberlo. Fuera
# de `_ACCIONES_SIN_ORDEN` a propósito, por la misma razón que `nacio_cancelada`.
ACCION_CANCELADA_SIN_ORDEN = "cancelada_sin_orden"
# Se acabó la ventana y la guía nunca llegó. SALE de las dos colas —ya no se va
# a crear, ya no esconde stock— pero NO desaparece: se queda visible en el panel
# en rojo. Una fila que se esfuma al cumplir 14 días es una venta que nadie
# supo nunca que existió.
ACCION_ESPERA_CADUCADA = "espera_caducada"
# La orden EXISTE en Odoo pero se quedó en borrador: no reserva. Para el stock
# cuenta igual que si no existiera — ver `piezas_sin_orden`.
ACCION_SIN_RESERVA = "no_se_pudo_confirmar"

# Acciones que sólo MIRAN: nadie escribió en Odoo y nadie lo va a hacer por
# ellas. Son las que NO pueden pisar un `espera_guia`; ver `_GUARDA_ESPERA`.
_ACCIONES_SIMULACION = ("simulado", "apagado", "canal_apagado", "solo_registro")

# ⚠️ EL CANDADO DE LA COLA. Desde que la creación es diferida, `accion` dejó de
# ser una etiqueta descriptiva y pasó a ser LA LLAVE de dos colas:
# `pendientes_sin_orden` (quién todavía tiene que nacer en Odoo) y
# `piezas_sin_orden` (cuánto stock hay que esconder mientras tanto). Las dos
# filtran por `accion = 'espera_guia'`.
#
# Y el UPSERT de abajo lo corre CUALQUIERA, incluido
# `POST /api/automatizacion/backfill`, que reproduce la semana pasada con
# `crear_orden(dry_run=True)` y por tanto devuelve `accion='simulado'`. Sin este
# candado, correr el backfill sobre una venta que espera guía la sacaba de las
# DOS colas de golpe: su orden no nacía nunca en Odoo (el trabajo de guías ya no
# la veía), el almacén no se enteraba de la venta, y además su stock volvía al
# anaquel de Woo y de los canales. `vincular_sin_orden` tampoco la rescataba,
# porque nunca iba a existir una orden que vincular. Sin log y sin contador: se
# descubría cuando reclamaba el cliente.
#
# Es exactamente la lección de `odoo_ventas.pendientes_de_guia` con otra
# columna: una fila no puede salir de la cola sin que el HECHO haya ocurrido.
# Así que una acción de simulación NUNCA pisa una espera viva. Lo que sí pasa:
# `crear_con_guia` trae `odoo_order_id` (el hecho) y entra;
# `marcar_cancelada_sin_orden` es un UPDATE aparte y no pasa por aquí; y
# cualquier acción que no sea de simulación (`error`, `sku_sin_producto`,
# `sin_orden`…) también entra, porque dice algo que la fila no sabía.
_GUARDA_ESPERA = (
    f"ops.odoo_sale_orders.accion = '{ACCION_ESPERA}' "
    "and ops.odoo_sale_orders.odoo_order_id is null "
    "and excluded.odoo_order_id is null "
    f"and excluded.accion in ({', '.join(chr(39) + a + chr(39) for a in _ACCIONES_SIMULACION)})")


def registrar(canal: str, cuenta: str, order_id: str, resultado: dict[str, Any],
              items: list[dict[str, Any]] | None = None,
              guia: str = "", paqueteria: str = "",
              refrescar_plan: bool = False, intentos: int = 3) -> bool:
    """
    Guarda (o actualiza) el renglón de esta venta y sus líneas.

    `resultado` es lo que devolvió `odoo_ventas.crear_orden`/`cancelar_orden`.
    `items` son las líneas normalizadas de la venta — de ahí salen la imagen y
    las unidades; el stock sale de `resultado["stock_foto"]`.

    LA LLAVE ES (canal, cuenta, external_order_id). La `cuenta` entra porque un
    id de orden solo es único DENTRO de una cuenta: el mismo canal con dos
    tiendas puede repetir el número, y sin ella la segunda venta pisaría a la
    primera. Es la misma llave que ya usa `channel.orders`.

    `refrescar_plan=True` deja escribir ADEMÁS almacén, cobertura y total. Es
    para la creación diferida (`odoo_ventas.crear_con_guia`) y sólo para ella:
    cuando la orden nace uno o dos días después de la venta, lo que hay en esas
    columnas es un plan que NUNCA se ejecutó, y dejarlo ahí mandaría al almacén
    a la bodega equivocada. No confundir con la foto de stock: ésa sigue siendo
    intocable y no se re-escribe ni con esto (ver el UPSERT de las líneas).

    `intentos` REINTENTA el escritorio entero antes de rendirse, y desde la
    creación diferida eso dejó de ser un lujo. Antes esta fila era una bitácora:
    si kubera tropezaba, se perdía la foto de stock y nada más — la orden ya
    estaba en Odoo y `pendientes_de_guia` (que le pregunta a ODOO) la seguía
    viendo. Ahora la fila ES LA COLA: sin ella la venta no existe para nadie, no
    hay orden, no hay espacio en el panel, el sondeo no vuelve a pasar por ella
    (salta lo ya registrado) y su stock se ofrece otra vez. Un tropiezo de dos
    minutos del pooler bastaba. El reintento tapa el parpadeo; el hueco largo lo
    tapa `odoo_ventas.reponer_espacios`, que vuelve a mirar `channel.orders`.

    ⚠️ BLOQUEA (psycopg2 y, ahora, `time.sleep` entre intentos): desde un hilo.
    El seam ya la llama con `asyncio.to_thread` (regla 11).
    """
    import time

    from services import supabase_db as sdb

    foto = resultado.get("stock_foto") or {}
    # El plan SÓLO se re-escribe si el que llama lo pidió Y trae uno. Un
    # `coalesce` sin la bandera dejaría pasar cualquier re-registro; con la
    # bandera y sin valor, se conserva lo que había.
    plan_sql = ("""
                        almacen_id    = coalesce(excluded.almacen_id,
                                                 ops.odoo_sale_orders.almacen_id),
                        almacen       = coalesce(nullif(excluded.almacen, ''),
                                                 ops.odoo_sale_orders.almacen),
                        cobertura     = coalesce(nullif(excluded.cobertura, ''),
                                                 ops.odoo_sale_orders.cobertura),
                        total         = coalesce(excluded.total,
                                                 ops.odoo_sale_orders.total),"""
                if refrescar_plan else "")

    def _escribir() -> None:
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
                        -- EL CANDADO DE LA COLA (ver `_GUARDA_ESPERA`): una
                        -- acción de SIMULACIÓN no puede sacar de la cola a una
                        -- venta que espera su guía. Va en los tres campos que
                        -- describen el desenlace, porque dejar la acción y
                        -- pisarle el motivo contaría dos historias distintas.
                        estado        = case when {_GUARDA_ESPERA}
                                             then ops.odoo_sale_orders.estado
                                             else excluded.estado end,
                        accion        = case when {_GUARDA_ESPERA}
                                             then ops.odoo_sale_orders.accion
                                             else excluded.accion end,
                        -- La guía puede llegar VACÍA en el primer aviso y con
                        -- valor después; nunca al revés. `nullif`+`coalesce`
                        -- deja pasar '' → valor y bloquea valor → ''.
                        guia          = coalesce(nullif(excluded.guia, ''),
                                                 ops.odoo_sale_orders.guia),
                        paqueteria    = coalesce(nullif(excluded.paqueteria, ''),
                                                 ops.odoo_sale_orders.paqueteria),
                        motivo        = case when {_GUARDA_ESPERA}
                                             then ops.odoo_sale_orders.motivo
                                             else excluded.motivo end,{plan_sql}
                        actualizado_at = now()
                    -- almacen/cobertura/total NO se re-tocan salvo con
                    -- `refrescar_plan`: son de la decisión original y describen
                    -- el momento en que se creó la orden.
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

    # El reintento es CORTO a propósito: corre en el camino de una venta (en un
    # hilo, pero el sondeo del canal espera). Medio segundo y uno y medio tapan
    # el parpadeo del pooler; un corte de dos minutos no se tapa aquí y por eso
    # existe `reponer_espacios`.
    ultimo: Exception | None = None
    for n in range(max(1, int(intentos))):
        try:
            _escribir()
            if n:
                log.info("odoo_ventas_log.registrar(%s, %s): quedó al intento %s",
                         canal, order_id, n + 1)
            return True
        except Exception as exc:  # noqa: BLE001 — una bitácora no tumba una venta
            ultimo = exc
            if n + 1 < max(1, int(intentos)):
                time.sleep(0.5 * (n + 1))
    log.warning("odoo_ventas_log.registrar(%s, %s): %s intento(s) y no se pudo: %s. "
                "Si esta venta esperaba guía, su ESPACIO no quedó escrito y sólo "
                "`reponer_espacios` la recupera.", canal, order_id, intentos, ultimo)
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

    TAMPOCO RETROCEDE UN SURTIDO DIVIDIDO: si la fila dice "G1 + G2" (una venta
    en dos cajas, lo escribe el refresco de guías) y llega "G1" —el re-aviso del
    canal trae la guía del PRIMER paquete—, se deja "G1 + G2". Sólo aplica a
    filas cuya guía ya trae " + "; las demás siguen igual que siempre.
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
                       or coalesce(paqueteria, '') is distinct from %(p)s)
                  and not (%(g)s <> '' and position(' + ' in coalesce(guia, '')) > 0
                           and %(g)s = any(string_to_array(guia, ' + ')))""",
            {"c": canal, "cu": cuenta, "o": str(order_id),
             "g": guia or "", "p": paqueteria or ""})
        return bool(n)
    except Exception as exc:  # noqa: BLE001
        log.debug("odoo_ventas_log.actualizar_guia(%s, %s): %s", canal, order_id, exc)
        return False


# Acciones que dicen "no hay orden en Odoo" por un TROPIEZO que alguien puede
# arreglar después: SKU sin producto, error, o el automatismo apagado/observando
# en ese momento. NO van las que dicen "no debía haber orden" (nació cancelada,
# sin orden al cancelar): vincularlas pegaría una venta muerta a una orden viva.
#
# `espera_guia` SÍ va, y es una decisión: esa venta es una venta VIVA cuya orden
# todavía no nace. Si Gabriela se adelanta y la captura a mano, vincularla es lo
# correcto —la fila cuenta la verdad y sale de la cola de espera por el HECHO de
# que la orden existe—. Y no puede duplicar: `crear_orden` empieza por
# `buscar_por_ref` y devolvería `ya_existia`.
#
# `espera_caducada` TAMBIÉN va: la guía nunca llegó y nosotros ya no la vamos a
# crear, pero si alguien la captura a mano la fila tiene que enterarse. Es el
# caso en que vincular es MÁS necesario, no menos.
_ACCIONES_SIN_ORDEN = ("sku_sin_producto", "error", "apagado", "canal_apagado",
                       "solo_registro", "simulado", "espera_guia",
                       "espera_caducada")


def pendientes_sin_orden(canal: str, dias: int = 14,
                         limite: int = 60) -> list[dict[str, Any]]:
    """
    Las ventas que sólo tienen su ESPACIO: esperan la guía para nacer en Odoo.

    ⚠️ BLOQUEA (psycopg2): llamar desde un hilo.

    LA CONDICIÓN DE SALIDA ES `odoo_order_id is not null` — un HECHO DE ODOO— y
    NUNCA la columna `guia`. Esto no es estilo: la primera versión de la cola de
    guías filtraba por `guia` y, como el seam de la venta rellena esa columna en
    cualquier re-aviso sin tocar Odoo, las filas salían de la cola con la
    entrega vacía para siempre (está contado en `odoo_ventas.pendientes_de_guia`).
    Aquí el mismo error sería peor: la venta saldría de la cola SIN ORDEN, y el
    almacén nunca se enteraría de ella.

    Devuelve renglones con la forma de la cola de guías —`pickings` y `sin_pdf`
    vacíos, porque todavía no hay nada en Odoo— más `espera_guia=True` y la
    ANTIGÜEDAD, que es lo que hace visible en el panel a una venta que lleva
    demasiado esperando. Sin datos del comprador. Nunca lanza.

    ⚠️ SE REPARTE ENTRE LO NUEVO Y LO VIEJO, y no es un detalle de estilo.
    Con `order by creado_at asc` a secas, las ventas que NUNCA resuelven se
    quedan pegadas a la cabeza de la cola los 14 días enteros. Y en Temu ese
    relleno está garantizado: `_ESTADOS_WC` sólo mapea {2,4,5}, así que una
    venta en cualquier otro estado no se crea, no se marca y no sale. En cuanto
    hay más esperando que el tope de la vuelta, las ventas NUEVAS —las que
    justamente van a recibir su guía en las próximas horas, con la mediana de
    Temu en 28.4 h— dejan de mirarse hasta que las viejas caduquen… y para
    entonces la nueva también caducó. Reproducido con 200 viejas + 1 de hoy:
    doce vueltas seguidas devolvían las MISMAS 60 viejas.
    Así que la mitad del cupo es para las más NUEVAS y la otra mitad para las
    más VIEJAS: lo nuevo entra siempre, y la cola vieja no se abandona.
    """
    from services import supabase_db as sdb

    canal = (canal or "").lower()
    if not canal:
        return []
    tope = max(1, int(limite))
    nuevas = (tope + 1) // 2          # la mitad de arriba, redondeando a favor
    try:
        filas = sdb.fetch_all(
            """with cola as (
                 select cuenta, external_order_id, creado_at, motivo, almacen,
                        cobertura,
                        extract(epoch from (now() - creado_at)) / 3600.0 as horas,
                        row_number() over (order by creado_at desc) as rn_nueva,
                        row_number() over (order by creado_at asc)  as rn_vieja
                   from ops.odoo_sale_orders
                  where canal = %(c)s and odoo_order_id is null
                    and accion = %(a)s
                    and creado_at > now() - make_interval(days => %(d)s))
               select cuenta, external_order_id, creado_at, motivo, almacen,
                      cobertura, horas
                 from cola
                where rn_nueva <= %(nuevas)s or rn_vieja <= %(viejas)s
                order by creado_at asc limit %(l)s""",
            {"c": canal, "a": ACCION_ESPERA, "d": int(dias), "l": tope,
             "nuevas": nuevas, "viejas": tope - nuevas})
    except Exception as exc:  # noqa: BLE001
        log.warning("pendientes_sin_orden(%s): %s", canal, str(exc)[:150])
        return []
    return [{"order_id": str(f["external_order_id"]), "cuenta": f["cuenta"],
             "espera_guia": True, "pickings": [], "sin_pdf": [], "ordenes": [],
             "creado_at": f["creado_at"], "antiguedad_h": float(f["horas"] or 0),
             "almacen_al_vender": f["almacen"], "cobertura_al_vender": f["cobertura"]}
            for f in filas]


def caducar_esperas(canal: str, dias: int, limite: int = 500) -> dict[str, Any]:
    """
    Las esperas que se pasaron de la ventana dejan de esperar EN VOZ ALTA.
    ⚠️ BLOQUEA. Nunca lanza.

    Sin esto, una venta que cumple los `dias` simplemente deja de aparecer: sale
    de la cola de creación y de la resta de stock porque las dos consultas
    filtran por fecha, y en el panel se queda en cian diciendo "Esperando guía"
    para siempre, cuando ya no la espera nadie. Es la misma forma de fallar que
    este cambio vino a corregir: una fila que se va de la cola sin que el hecho
    haya ocurrido.

    Marcarla `espera_caducada` la saca de las dos colas POR SU ACCIÓN (no por
    una fecha que nadie ve), la pinta en rojo, y la deja vinculable si alguien
    la captura a mano. No toca Odoo: ahí nunca hubo nada.
    """
    from services import supabase_db as sdb
    try:
        n = sdb.execute(
            """update ops.odoo_sale_orders
                  set accion = %(cad)s, motivo = %(m)s, actualizado_at = now()
                where canal = %(c)s and odoo_order_id is null
                  and accion = %(a)s
                  and creado_at <= now() - make_interval(days => %(d)s)
                  and external_order_id in (
                      select external_order_id from ops.odoo_sale_orders
                       where canal = %(c)s and odoo_order_id is null
                         and accion = %(a)s
                         and creado_at <= now() - make_interval(days => %(d)s)
                       order by creado_at asc limit %(l)s)""",
            {"c": (canal or "").lower(), "a": ACCION_ESPERA,
             "cad": ACCION_ESPERA_CADUCADA, "d": int(dias), "l": int(limite),
             "m": (f"Pasaron {int(dias)} días y el canal nunca dio la guía: la "
                   "orden en Odoo ya NO se va a crear sola y sus piezas vuelven "
                   "al anaquel. Si la venta es real, hay que capturarla a mano.")})
        if n:
            log.warning("Odoo %s: %s venta(s) llevaban más de %s días esperando "
                        "guía y se marcaron CADUCADAS: su orden no nacerá sola.",
                        canal, n, dias)
        return {"canal": canal, "caducadas": int(n or 0)}
    except Exception as exc:  # noqa: BLE001
        log.warning("caducar_esperas(%s): %s", canal, str(exc)[:150])
        return {"canal": canal, "caducadas": 0, "error": str(exc)[:150]}


def contar_esperas(canal: str | None = None, dias: int = 14) -> dict[str, Any]:
    """
    Cuántas ventas están esperando su guía y desde cuándo. ⚠️ BLOQUEA.

    Existe para poder decir un NÚMERO donde hoy sólo hay un aviso. Cuando
    alguien apaga el trabajo de guías con la espera encendida, las ventas nuevas
    vuelven a crearse al vender (falla cerrado) pero las que YA estaban en la
    cola no las retoma nadie: siguen escondiendo stock hasta caducar y después
    desaparecen sin orden. Un chip ámbar que dice "espera pedida, pero inactiva"
    suena a que no pasa nada; "9 ventas sin quién las cree" no. Nunca lanza.
    """
    from services import supabase_db as sdb
    donde = ["odoo_order_id is null", "accion = %(a)s",
             "creado_at > now() - make_interval(days => %(d)s)"]
    params: dict[str, Any] = {"a": ACCION_ESPERA, "d": int(dias)}
    if canal:
        donde.append("canal = %(c)s")
        params["c"] = canal.lower()
    try:
        f = sdb.fetch_one(
            f"""select count(*) as ventas,
                       coalesce(extract(epoch from (now() - min(creado_at)))
                                / 3600.0, 0) as horas
                  from ops.odoo_sale_orders
                 where {' and '.join(donde)}""", params) or {}
        return {"ventas": int(f.get("ventas") or 0),
                "mas_vieja_h": round(float(f.get("horas") or 0), 1)}
    except Exception as exc:  # noqa: BLE001
        log.debug("contar_esperas(%s): %s", canal, exc)
        return {"ventas": 0, "mas_vieja_h": 0.0, "error": str(exc)[:150]}


def ventas_sin_fila(canal: str, horas: int = 48,
                    limite: int = 200) -> list[dict[str, Any]]:
    """
    Ventas de `channel.orders` que NO tienen renglón en la bitácora. ⚠️ BLOQUEA.

    EL AGUJERO QUE TAPA. Con la creación diferida, esta tabla dejó de ser una
    bitácora y pasó a ser el ÚNICO camino por el que una venta llega a Odoo. Y
    ese camino falla en silencio: `registrar` devuelve False y el seam se lo
    traga. Si kubera tropieza en el instante de la venta no queda fila, no hay
    "espacio", la cola no la ve, `vincular_sin_orden` tampoco (sólo mira filas
    que existen) y su stock se ofrece otra vez. El sondeo no vuelve a pasar por
    ella. Antes el mismo fallo era inocuo: la orden ya estaba en Odoo y
    `pendientes_de_guia` —que le pregunta a ODOO— la seguía viendo.

    Trae la venta con sus líneas, listas para `crear_orden`. SIN datos del
    comprador: sólo id, cuenta, fecha y renglones. Nunca lanza.
    """
    from services import supabase_db as sdb
    try:
        return [dict(f) for f in sdb.fetch_all(
            """select o.external_order_id as order_id, o.cuenta, o.creado_at,
                      o.estado_wc,
                      coalesce((select json_agg(json_build_object(
                            'sku', i.sku::text, 'cantidad', i.cantidad,
                            'precio_unitario', i.precio_unitario,
                            'titulo', i.titulo) order by i.linea)
                          from channel.order_items i
                         where i.canal = o.canal
                           and i.external_order_id = o.external_order_id),
                        '[]'::json) as items
                 from channel.orders o
                where o.canal = %(c)s
                  and o.creado_at > now() - make_interval(hours => %(h)s)
                  and not exists (select 1 from ops.odoo_sale_orders b
                                   where b.canal = o.canal and b.cuenta = o.cuenta
                                     and b.external_order_id = o.external_order_id)
                order by o.creado_at asc limit %(l)s""",
            {"c": (canal or "").lower(), "h": int(horas), "l": int(limite)})]
    except Exception as exc:  # noqa: BLE001
        log.warning("ventas_sin_fila(%s): %s", canal, str(exc)[:150])
        return []


def fijar_orden(canal: str, cuenta: str, order_id: str, odoo_id: int,
                nombre: str = "", estado: str = "", accion: str = "",
                motivo: str = "") -> bool:
    """
    Anota SÓLO que la orden ya existe en Odoo. ⚠️ BLOQUEA. Nunca lanza.

    ES EL REINTENTO MÍNIMO de `registrar`, y tapa el peor cruce que tiene la
    creación diferida: la orden nace en Odoo —Odoo YA reservó— y justo entonces
    kubera no contesta al `registrar`. `crear_con_guia` sigue adelante a
    propósito (la orden existe, y eso es lo que no se puede perder), pero la
    fila se queda `espera_guia` con `odoo_order_id` NULL… y `piezas_sin_orden`
    sigue restando sus piezas ENCIMA de la reserva de Odoo. No es un cruce de
    una pasada que se cura solo: la fila no sale de la cola, así que la resta se
    repite cada 20 minutos hasta que caduque o corra `vincular_sin_orden`.

    Este UPDATE escribe cuatro columnas y ninguna línea, así que tiene mucha más
    probabilidad de pasar que el UPSERT completo. Sólo toca filas SIN orden:
    re-correrlo no pisa nada.
    """
    from services import supabase_db as sdb
    try:
        n = sdb.execute(
            """update ops.odoo_sale_orders
                  set odoo_order_id = %(id)s,
                      odoo_name = coalesce(nullif(%(n)s, ''), odoo_name),
                      estado = coalesce(nullif(%(e)s, ''), estado),
                      accion = coalesce(nullif(%(a)s, ''), accion),
                      motivo = coalesce(nullif(%(m)s, ''), motivo),
                      actualizado_at = now()
                where canal = %(c)s and cuenta = %(cu)s
                  and external_order_id = %(o)s and odoo_order_id is null""",
            {"id": int(odoo_id), "n": (nombre or "")[:120], "e": (estado or "")[:60],
             "a": (accion or "")[:40], "m": (motivo or "")[:300],
             "c": (canal or "").lower(), "cu": cuenta, "o": str(order_id)})
        return bool(n)
    except Exception as exc:  # noqa: BLE001
        log.warning("fijar_orden(%s, %s): %s", canal, order_id, str(exc)[:150])
        return False


def plan_guardado(canal: str, cuenta: str, order_id: str) -> dict[str, Any] | None:
    """El almacén y la cobertura que se decidieron EL DÍA DE LA VENTA, más
    cuándo fue. ⚠️ BLOQUEA. Sólo para poder decir en el motivo si el plan de hoy
    es el mismo; no decide nada. Nunca lanza."""
    from services import supabase_db as sdb
    try:
        return sdb.fetch_one(
            """select almacen, cobertura, creado_at, accion
                 from ops.odoo_sale_orders
                where canal = %(c)s and cuenta = %(cu)s
                  and external_order_id = %(o)s""",
            {"c": (canal or "").lower(), "cu": cuenta, "o": str(order_id)})
    except Exception as exc:  # noqa: BLE001
        log.debug("plan_guardado(%s, %s): %s", canal, order_id, exc)
        return None


def anotar_intento(canal: str, cuenta: str, order_id: str, motivo: str) -> bool:
    """
    Deja dicho que esta vuelta intentó crear la orden y no pudo. ⚠️ BLOQUEA.

    NO cambia la acción: la fila tiene que SEGUIR en la cola de espera para que
    la vuelta siguiente lo reintente. Sólo escribe el motivo y la hora, que es
    lo que hace visible en el panel una venta que lleva varias vueltas fallando
    en vez de una que simplemente no tiene guía todavía. Nunca lanza.
    """
    from services import supabase_db as sdb
    try:
        sdb.execute(
            """update ops.odoo_sale_orders
                  set motivo = %(m)s, actualizado_at = now()
                where canal = %(c)s and cuenta = %(cu)s
                  and external_order_id = %(o)s and odoo_order_id is null""",
            {"c": (canal or "").lower(), "cu": cuenta, "o": str(order_id),
             "m": f"Ya hay guía pero la orden no se pudo crear: {motivo}"[:300]})
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("anotar_intento(%s, %s): %s", canal, order_id, exc)
        return False


def marcar_cancelada_sin_orden(canal: str, cuenta: str, order_id: str,
                               estado: str = "") -> bool:
    """
    La venta se canceló mientras esperaba su guía: sale de la cola y NO se crea.
    ⚠️ BLOQUEA. Nunca lanza.

    Es la mitad buena del cambio: hoy TikTok cancela el 58% de sus ventas y cada
    una deja una orden confirmada que el almacén tiene que aprender a ignorar.
    Con la creación diferida, esa orden simplemente nunca existe.

    Sólo toca filas SIN orden. Si la orden ya nació —alguien la creó a mano, o
    la carrera se perdió por segundos— esto no la toca y la cancelación la
    atiende `odoo_ventas.cancelar_orden`, que es quien sabe hablar con Odoo.
    """
    from services import supabase_db as sdb
    try:
        n = sdb.execute(
            """update ops.odoo_sale_orders
                  set accion = %(a)s, estado = %(e)s, motivo = %(m)s,
                      actualizado_at = now()
                where canal = %(c)s and cuenta = %(cu)s
                  and external_order_id = %(o)s
                  and odoo_order_id is null and accion = %(esp)s""",
            {"c": (canal or "").lower(), "cu": cuenta, "o": str(order_id),
             "a": ACCION_CANCELADA_SIN_ORDEN, "e": (estado or "")[:60],
             "esp": ACCION_ESPERA,
             "m": ("El canal la canceló mientras esperaba su guía: no se crea "
                   "orden en Odoo" + (f" (estado {estado})" if estado else "") + ".")})
        if n:
            log.info("Odoo %s: la venta %s se canceló esperando guía — no se crea "
                     "orden", canal, order_id)
        return bool(n)
    except Exception as exc:  # noqa: BLE001
        log.debug("marcar_cancelada_sin_orden(%s, %s): %s", canal, order_id, exc)
        return False


def piezas_sin_orden(canal: str | None = None, dias: int = 14) -> dict[str, float]:
    """
    {sku: piezas} comprometidas por ventas que TODAVÍA NO tienen orden en Odoo.
    ⚠️ BLOQUEA. Nunca lanza: si algo falla devuelve {}, que es no restar nada.

    PARA QUÉ. Mientras la orden no nace, Odoo no reserva: `free_qty` no baja,
    `stock_watch` copia ese número a Woo y el fan-out devuelve al anaquel la
    pieza que alguien ya compró. Esto es lo que hay que RESTAR antes de copiar
    (`destino = max(0, free_qty − pendientes)`), y es la parte C del encargo.

    Deja fuera a propósito lo que no está vendido-y-sin-surtir: sólo
    `accion='espera_guia'`, sólo sin `odoo_order_id` y sólo dentro de la
    ventana. El tope de días importa: una venta muerta que nadie canceló no
    puede esconder stock para siempre.

    ⚠️ CON UNA EXCEPCIÓN: `no_se_pudo_confirmar`. El relevo resta→reserva se
    dispara con "ya hay orden", pero una orden puede EXISTIR SIN RESERVAR: si
    Odoo la deja en borrador, `crear_orden` devuelve esa acción con el
    `odoo_id` lleno, la fila recibe su `odoo_order_id` y la resta se apagaría…
    sin que `free_qty` hubiera bajado una sola pieza. El diseño promete que "la
    reserva real toma el relevo", y en ese caso la reserva nunca llega. Así que
    la condición de salida es el HECHO COMPLETO —orden que además reserva— y no
    sólo el id. Medido: 0 de 109 filas en 90 días; raro, no imposible.
    """
    from services import supabase_db as sdb
    donde = ["""((o.odoo_order_id is null and o.accion = %(a)s)
                 or o.accion = %(nc)s)""",
             "o.creado_at > now() - make_interval(days => %(d)s)"]
    params: dict[str, Any] = {"a": ACCION_ESPERA, "nc": ACCION_SIN_RESERVA,
                              "d": int(dias)}
    if canal:
        donde.append("o.canal = %(c)s")
        params["c"] = canal.lower()
    try:
        filas = sdb.fetch_all(
            f"""select i.sku, sum(i.cantidad) as piezas
                  from ops.odoo_sale_orders o
                  join ops.odoo_sale_order_items i
                    on i.canal = o.canal and i.cuenta = o.cuenta
                   and i.external_order_id = o.external_order_id
                 where {' and '.join(donde)}
                 group by i.sku""", params)
        return {str(f["sku"]): float(f["piezas"] or 0) for f in filas
                if str(f["sku"] or "").strip()}
    except Exception as exc:  # noqa: BLE001
        log.warning("piezas_sin_orden: %s", str(exc)[:150])
        return {}


def centinela_pendientes(canal: str | None = None,
                         dias: int = 14) -> dict[str, Any]:
    """
    El CENTINELA de la resta de stock: cuántas ventas están comprometidas y
    cuántas de ellas tienen renglones. ⚠️ BLOQUEA. **LANZA** si no puede
    responder — es su razón de existir.

    POR QUÉ HAY QUE CONTAR LOS RENGLONES APARTE. `piezas_sin_orden` nunca lanza:
    ante cualquier fallo devuelve `{}`, que significaría "no hay nada vendido
    pendiente" y mandaría copiar `free_qty` tal cual. Así que se pregunta dos
    veces… pero "vino vacío" tenía DOS causas y se trataban igual:

      · kubera se cayó entre las dos consultas → transitorio, hay que frenar;
      · una venta esperando no tiene renglones → PERMANENTE y alcanzable:
        `registrar` salta las líneas con `cantidad <= 0` o SKU vacío pero SÍ
        escribe el encabezado. Una sola venta así frenaba el tramo Odoo→Woo
        ENTERO cada 20 minutos durante los 14 días de la ventana: una recepción
        de mercancía no llegaba nunca a la tienda y un SKU agotado en Odoo no
        bajaba nunca a 0 en Woo. Catorce días sin sincronizar inventario hace
        más daño que la pieza que se intentaba esconder.

    Con `ventas_con_items` las dos se distinguen sin adivinar: si NINGUNA de las
    ventas contadas tiene renglones, un desglose vacío es la respuesta CORRECTA
    (no hay nada que restar) y la pasada sigue. Si alguna los tiene y el
    desglose llega vacío, sí se rompió algo y se falla cerrado.

    Es el mismo `where` que `piezas_sin_orden`, en la misma consulta agregada.
    """
    from services import supabase_db as sdb
    donde = ["""((o.odoo_order_id is null and o.accion = %(a)s)
                 or o.accion = %(nc)s)""",
             "o.creado_at > now() - make_interval(days => %(d)s)"]
    params: dict[str, Any] = {"a": ACCION_ESPERA, "nc": ACCION_SIN_RESERVA,
                              "d": int(dias)}
    if canal:
        donde.append("o.canal = %(c)s")
        params["c"] = canal.lower()
    f = sdb.fetch_one(
        f"""select count(*) as ventas,
                   count(*) filter (where o.renglones > 0) as ventas_con_items,
                   coalesce(extract(epoch from (now() - min(o.creado_at)))
                            / 3600.0, 0) as horas,
                   (array_agg(o.canal || ' ' || o.external_order_id
                              order by o.creado_at asc))[1:10] as muestra
              from (select o.*,
                           (select count(*) from ops.odoo_sale_order_items i
                             where i.canal = o.canal and i.cuenta = o.cuenta
                               and i.external_order_id = o.external_order_id)
                           as renglones
                      from ops.odoo_sale_orders o) o
             where {' and '.join(donde)}""", params) or {}
    return {"ventas": int(f.get("ventas") or 0),
            "ventas_con_items": int(f.get("ventas_con_items") or 0),
            "mas_vieja_h": round(float(f.get("horas") or 0), 1),
            # Números de venta, JAMÁS datos del comprador.
            "muestra": list(f.get("muestra") or [])}


def vincular_sin_orden(canal: str, dias: int = 30, limite: int = 200) -> dict[str, Any]:
    """
    Vincula a su orden de Odoo las ventas que la bitácora dejó SIN orden. ⚠️ BLOQUEA.

    POR QUÉ. `registrar` sólo corre cuando la automatización crea la orden. Si
    no pudo —el caso real: la venta 586126707939116455 de TikTok vendió el SKU
    PADRE ROP-0256, que no existe en Odoo— y alguien la crea aparte con el
    número de venta como referencia (S38923 con la variante ROP-0256-NAR-XL,
    18-sep), la bitácora nunca se entera: el tab seguía diciendo "Error · SKU
    sin producto" con la orden viva en Odoo.

    Busca en Odoo, con el partner del canal y sin canceladas, órdenes cuyo
    `client_order_ref` sea la venta o `<venta>#n` (surtido dividido), y llena
    la fila: id, nombre(s), estado, almacén(es) y una acción que dice lo que
    Odoo tiene ("confirmada" o "creada"), con un motivo que cuenta que se
    vinculó y qué decía antes. SÓLO escribe la bitácora, nunca Odoo, y SÓLO
    filas sin `odoo_order_id`: re-correrlo no pisa nada. Nunca lanza.
    """
    from services import odoo_ventas
    from services import supabase_db as sdb

    r: dict[str, Any] = {"canal": canal, "revisadas": 0, "vinculadas": 0, "ventas": []}
    partner = odoo_ventas._PARTNER.get(canal)
    if not partner:
        return {**r, "error": f"canal '{canal}' sin partner"}
    try:
        filas = sdb.fetch_all(
            """select cuenta, external_order_id, accion from ops.odoo_sale_orders
                where canal = %(c)s and odoo_order_id is null
                  and accion = any(%(a)s)
                  and creado_at > now() - make_interval(days => %(d)s)
                order by creado_at desc limit %(l)s""",
            {"c": canal, "a": list(_ACCIONES_SIN_ORDEN), "d": int(dias), "l": int(limite)})
    except Exception as exc:  # noqa: BLE001
        log.warning("vincular_sin_orden(%s): no se pudo leer la bitácora: %s",
                    canal, str(exc)[:150])
        return {**r, "error": f"bitácora: {str(exc)[:150]}"}
    r["revisadas"] = len(filas)
    if not filas:
        return r

    antes = {str(f["external_order_id"]): (f["cuenta"], f["accion"]) for f in filas}
    ventas = list(antes)
    # (ref exacta) | (ref =like venta#%) | … y además partner y no cancelada.
    terminos: list[Any] = [["client_order_ref", "in", ventas]]
    terminos += [["client_order_ref", "=like", f"{v}#%"] for v in ventas]
    dominio = (["|"] * (len(terminos) - 1) + terminos
               + [["partner_id", "=", partner], ["state", "!=", "cancel"]])
    try:
        ords = odoo_ventas._kw("sale.order", "search_read", [dominio],
                               {"fields": ["id", "name", "client_order_ref", "state",
                                           "warehouse_id"],
                                "order": "id asc"})
    except Exception as exc:  # noqa: BLE001
        log.warning("vincular_sin_orden(%s): Odoo no contestó: %s", canal, str(exc)[:150])
        return {**r, "error": f"odoo: {str(exc)[:150]}"}

    por_venta: dict[str, list[dict[str, Any]]] = {}
    for o in ords or []:
        v = str(o.get("client_order_ref") or "").split("#", 1)[0]
        if v in antes:
            por_venta.setdefault(v, []).append(o)

    for v, partes in por_venta.items():
        partes.sort(key=lambda o: (str(o.get("client_order_ref") or ""), o["id"]))
        confirmadas = all(o.get("state") in ("sale", "done") for o in partes)
        accion = "confirmada" if confirmadas else "creada"
        nombres = " + ".join(str(o.get("name")) for o in partes)
        almacenes = " + ".join(str((o.get("warehouse_id") or [None, "—"])[1]) for o in partes)
        alm_id = (partes[0].get("warehouse_id") or [None])[0]
        cuenta, accion_vieja = antes[v]
        try:
            n = sdb.execute(
                """update ops.odoo_sale_orders
                      set odoo_order_id = %(id)s, odoo_name = %(n)s, estado = %(e)s,
                          accion = %(a)s, almacen_id = %(ai)s, almacen = %(al)s,
                          cobertura = %(cob)s, motivo = %(m)s, actualizado_at = now()
                    where canal = %(c)s and cuenta = %(cu)s
                      and external_order_id = %(v)s and odoo_order_id is null""",
                {"id": partes[0]["id"], "n": nombres, "e": partes[0].get("state") or "",
                 "a": accion, "ai": alm_id, "al": almacenes,
                 "cob": "dividida" if len(partes) > 1 else "completa",
                 "m": (f"Vinculada sola: {nombres} se creó aparte en Odoo con esta "
                       f"venta como referencia (antes: {accion_vieja})."),
                 "c": canal, "cu": cuenta, "v": v})
        except Exception as exc:  # noqa: BLE001
            log.warning("vincular_sin_orden(%s): no se pudo vincular %s: %s",
                        canal, v, str(exc)[:150])
            continue
        if n:
            r["vinculadas"] += 1
            r["ventas"].append({"venta": v, "orden": nombres, "accion": accion})
    if r["vinculadas"]:
        log.info("Bitácora %s: %s venta(s) vinculadas a su orden de Odoo: %s", canal,
                 r["vinculadas"], ", ".join(f"{x['venta']}→{x['orden']}" for x in r["ventas"]))
    return r


def historial(limite: int = 100, canal: str | None = None,
              solo_problemas: bool = False,
              dias: int | None = None) -> list[dict[str, Any]]:
    """Lo que pinta el tab: una fila por venta, con sus líneas anidadas."""
    from services import supabase_db as sdb

    donde, params = ["1=1"], {"lim": int(limite)}
    if canal:
        donde.append("o.canal = %(canal)s")
        params["canal"] = canal
    if dias:
        # Se acota por la fecha en que NOSOTROS la procesamos, no por la de
        # compra: "las últimas 24 h" quiere decir lo que el automatismo hizo
        # en 24 h, y ahí puede aparecer la cancelación de una venta de agosto.
        donde.append(f"o.creado_at >= now() - interval '{int(dias)} days'")
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
        #   no_se_pudo_confirmar      → la orden existe pero se quedó en
        #                               borrador: NO reserva, así que el stock
        #                               se sigue ofreciendo. Sobreventa viva
        #                               hasta que alguien la confirme a mano.
        #   espera_caducada           → pasaron los 14 días, el canal nunca dio
        #                               la guía y su orden ya no nace sola. Si
        #                               la venta es real hay que capturarla.
        #
        # La `cobertura parcial` se pide SÓLO de las que ya tienen orden: en una
        # espera ese dato es el plan del dry-run, que se recalcula al crear.
        donde.append("(o.accion in ('error','sku_sin_producto',"
                     "'no_se_pudo_cancelar','no_se_pudo_confirmar',"
                     "'espera_caducada') "
                     "or (o.cobertura = 'parcial' and o.odoo_order_id is not null))")
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
            f"""select accion, cobertura, (odoo_order_id is not null) as en_odoo,
                       count(*) n
                 from ops.odoo_sale_orders
                where {donde}
                group by 1, 2, 3""", params)
        # ⚠️ LOS CONTADORES SE ACOTAN AL HECHO DE ODOO. El rótulo dice "Órdenes
        # creadas", y con la creación diferida un `count(*)` a secas metía ahí
        # las `espera_guia` y las `cancelada_sin_orden` — ventas que NO tienen
        # orden en Odoo— justo al lado del KPI "Esperando guía", contando las
        # MISMAS filas con el rótulo contrario. Con Temu a mediana 28.4 h y
        # ~9-10 ventas en cola, eso no es un borde: es el estado normal.
        total = sum(f["n"] for f in filas if f["en_odoo"])
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
            # "Se crearon sin stock" sólo puede decirse de las que SE CREARON.
            # La cobertura de una fila en espera es el plan del dry-run del día
            # de la venta, y `crear_con_guia` lo vuelve a calcular con el stock
            # del día en que nazca la orden: alarmar por él es alarmar por una
            # reserva que ni se ha intentado.
            "parciales": sum(f["n"] for f in filas
                             if f["cobertura"] == "parcial" and f["en_odoo"]),
            "errores": sum(f["n"] for f in filas
                           if f["accion"] in ("error", "sku_sin_producto")),
            # Los ESPACIOS: ventas registradas cuya orden todavía no nace. Se
            # calcula aquí para que la pantalla no tenga que deducirlo de la
            # lista que trae (que está paginada y diría otro número).
            "espacios_30d": sum(f["n"] for f in filas
                                if f["accion"] == ACCION_ESPERA and not f["en_odoo"]),
            "caducadas_30d": sum(f["n"] for f in filas
                                 if f["accion"] == ACCION_ESPERA_CADUCADA),
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("odoo_ventas_log.resumen: %s", exc)
        return {"total_30d": 0, "por_accion": {}, "parciales": 0, "errores": 0,
                "espacios_30d": 0, "caducadas_30d": 0,
                "nota": "la tabla ops.odoo_sale_orders todavía no existe"}
