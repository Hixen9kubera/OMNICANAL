"""
Vigilante de inventario: cierra el círculo Odoo → Woo → canales.

POR QUÉ EXISTE (28-jul). La auditoría de la sincronización Odoo→Woo dejó ver dos
huecos que hacían imposible el "todo sincronizado":

1. **El fan-out solo se dispara con VENTAS.** Cuando llegan 198 piezas a Odoo y
   se empujan a Woo, ningún canal se entera: Amazon y ML se quedan como estaban.
   (Verificado: los únicos disparadores vivos eran `pedidos_ml` y `stock_full`,
   y éste último está apagado.) De paso, eso dejaba el *ratchet*: el fan-out
   podía llevar una publicación a 0 pero nunca revivirla al volver mercancía.

2. **El sync Odoo→Woo empuja el VALOR ABSOLUTO.** En julio eso era un defecto:
   Woo era la fuente de verdad de las VENTAS y Odoo no registraba esas bajas,
   así que `Woo = Odoo` resucitaba mercancía vendida. Se cambió a DELTAS.

DOS MODOS, Y EL CORRECTO DEPENDE DE QUIÉN MANDA (`STOCK_WATCH_ABSOLUTO`)
------------------------------------------------------------------------
El 20-ago Brandon fijó la arquitectura: **Odoo es el master**, Woo el
intermediario, los canales DROP el destino. Con esa regla el delta dejó de
servir, y el 27-ago se midió por qué:

  · **Arrastra la diferencia de base para siempre.** `JAR-0031-NEG` bajó 12 en
    Odoo y 12 en Woo —el delta se aplicó perfecto— y aun así quedó Odoo 0 vs
    Woo 23, porque venían de bases distintas. El delta nunca cierra esa brecha.
  · **No ve las RESERVAS.** `free_qty` = físico − reservado, y una orden en
    borrador reserva. `VIA-0024-NEG` tenía 30 piezas con 29 comprometidas —una
    vendible— y Woo ofrecía 14. Es la mercancía "vendida que no existe" que
    detectó Gaby.

    ABSOLUTO:  Odoo ──(free_qty)──► Woo ──► TikTok · Temu · ML   [vía fan-out]
    DELTA:     Odoo ──(variación)─► Woo ──► …            (modo anterior)

El absoluto solo es seguro porque Odoo registra las salidas. Si algún día Odoo
dejara de verlas, volvería el escenario de resurrección — y por eso el modo se
cambia con UNA VARIABLE, sin deploy.

El segundo tramo se dispara con CUALQUIER cambio de stock en Woo comparado
contra la foto anterior, venga de donde venga: venta, delta de Odoo, ingreso a
FULL, la compensación FULL/FBA o una edición a mano en wp-admin. Al ser
foto-contra-foto **no se puede evadir**, que es la diferencia con enganchar cada
escritor uno por uno.

COBERTURA: la foto de `odoo_watch` vive en `productos.stock_odoo`, que solo cubre
5,381 SKUs (tabla legada del robot, congelada). Ésta cubre el catálogo completo
(13,000 de Odoo / 14,422 de Woo), por eso guarda su propia foto.

LO VENDIDO QUE TODAVÍA NO TIENE ORDEN (`STOCK_WATCH_RESTA_PENDIENTES`, 23-sep)
------------------------------------------------------------------------------
Desde que la orden de Odoo nace CUANDO APARECE LA GUÍA y no al vender
(`ODOO_VENTAS_ESPERA_GUIA_CANALES`), hay una ventana de uno o dos días —Temu:
mediana 28.4 h, p75 63.3 h— en la que la venta existe y su orden no. Sin orden
no hay reserva, así que `free_qty` sigue contando la pieza vendida, el modo
ABSOLUTO la copia a Woo y el fan-out la devuelve al anaquel de los canales.

El arreglo es una resta de ESTE lado —Brandon fue explícito en que a Odoo no se
le escribe nada para apartar—:

    destino = max(0, free_qty_odoo − piezas_vendidas_sin_orden)

Las piezas salen de `odoo_ventas_log.piezas_sin_orden`: sólo filas con
`odoo_order_id IS NULL`, sólo `accion='espera_guia'`, sólo dentro de la ventana
de espera. Es UNA consulta por pasada, agregada por SKU; nunca una por SKU.

EL CRUCE (cuando la orden por fin nace) es lo delicado, porque durante unos
segundos podrían convivir la reserva de Odoo y esta resta. Se resuelve por el
ORDEN DE LAS DOS LECTURAS: primero los pendientes, después el catálogo de Odoo.
Con ese orden el único desajuste posible es restar de más por una pasada —la
pieza queda escondida 20 minutos y la pasada siguiente la devuelve—. Al revés
el desajuste sería ofrecer de más, que es exactamente lo que esto viene a
evitar. Se prefiere esconder a resucitar: está medido en
`scratchpad/test_stock_pendientes.py`.

  · SÓLO en modo ABSOLUTO. En DELTA la venta ya bajó Woo por su cuenta
    (`PEDIDOS_WC_DESCUENTA_STOCK=true` desde el día 1), y restar encima sería
    el doble descuento de verdad.
  · FALLA CERRADO: si no se puede MEDIR lo pendiente, la pasada no copia nada
    de Odoo a Woo. `piezas_sin_orden` nunca lanza —devuelve `{}` ante cualquier
    error— y aquí `{}` y "la base no contestó" tienen consecuencias opuestas.
    Ver `_pendientes`.

CANDADOS (nace apagado; encenderlo MUEVE INVENTARIO REAL — regla 3):
  · `STOCK_WATCH_ENABLED=false`      — no corre.
  · `STOCK_WATCH_SOLO_REGISTRO=true` — clasifica y ANOTA lo que haría, sin escribir.
  · `STOCK_WATCH_TOPE=300`           — cortacircuitos: si una pasada ve más
    cambios que el tope, NO aplica nada y avisa. Una edición masiva en Odoo (o un
    Odoo que responde vacío) no puede vaciar todos los canales de un golpe.
  · La PRIMERA pasada solo levanta la foto base: nunca escribe.

Todo queda anotado en `fanout_log` (misma bitácora del panel; TABLA TEMPORAL,
ver docs/TABLAS_TEMPORALES.md).

LA FUENTE (`STOCK_WATCH_FUENTE`, 15-oct — salida total de Odoo)
---------------------------------------------------------------
Desde las 18:34 UTC del 15-oct Odoo rechaza la cuenta API, y con `odoo` cada
pasada aborta en silencio. La salida de Woo tomó la RUTA DIRECTA (20-oct): en T0
el inventario sale del libro de kubera y Woo deja el camino del stock. El modo
puente `woo` (v0.654.0) nunca se desplegó y se quitó. La bandera elige:

  · `odoo` (por omisión) — todo lo de arriba, idéntico.
  · `libro` — `_pasada_libro`: SÓLO el segundo tramo, con el vendible del libro
    de kubera (`stock_libro.todos()`) en lugar del `_stock` de Woo. Compara
    contra el `stock_kubera` de la foto y, por cada diferencia, anota
    `vendible_cambio` y encola «cambio de vendible en kubera» SÓLO si el fan-out
    también lee el libro (`FANOUT_FUENTE=kubera`); si no, sólo registra (la
    sombra). La primera pasada mide contra el último `stock_woo`. Lo que el libro
    no trae vale 0 (D4): su `stock_kubera` se siembra en 0 y, si tenía un número
    distinto, es un cambio que se reparte como cualquier otro. Todo cuenta para el
    tope. El piso se mide en SKUs con piezas. NUNCA lee ni escribe Woo. La foto es
    `ops.stock_watch_photo` y nada más (sin MySQL): avanza `stock_kubera` y
    `actualizado`; `stock_odoo` se queda CONGELADO al 15-oct 18:34 (y está
    incompleto). Falla cerrada: el libro ilegible (`stock_libro.LibroNoDisponible`)
    o muy por debajo de la pasada anterior aborta la pasada entera; dos pasadas
    seguidas sin repartir avisan por Slack.
  · Cualquier otro valor (`woo` incluido) se NIEGA: log, aviso y nada más.

LA REVERSA de `libro` es `STOCK_WATCH_ENABLED=false` (o `STOCK_WATCH_SOLO_REGISTRO=
true`), NUNCA `STOCK_WATCH_FUENTE=odoo`: ver `kubera_decide`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from config import settings
from services import db

log = logging.getLogger("omnicanal.stock_watch")

# La foto vive en MySQL y se está mudando a `ops.stock_watch_photo` (PASO 2 de
# docs/PLAN_31_TABLAS.md). Mientras dure la mudanza el destino lo eligen dos
# flags: `SUPABASE_WRITE_STOCK_WATCH` (escribe en los dos) y luego
# `SUPABASE_READ_STOCK_WATCH` (la DECISIÓN pasa a kubera). Ver `kubera_escribe`
# y `kubera_decide` más abajo, y `services/stock_watch_read.py`.
_TABLA = "stock_watch_foto"

_ultimo: dict[str, Any] = {"estado": "sin_correr"}
_lock = asyncio.Lock()
# Epoch en que empezó la pasada en curso (o la última): con `_lock.locked()`, la matriz
# del fan-out sabe qué cambios anotó ESTA pasada y todavía no encola (`fanout_vivo`).
_pasada_desde: float | None = None


# La última pasada que leyó Odoo: desde ahí la columna `stock_odoo` de la foto no
# se mueve. Y NO es una foto completa: Odoo negó el acceso en el offset 5500 y
# esa pasada dejó NULL en 9,270 de 14,771 filas (medido en producción, 15-oct
# 21:35 UTC). Por eso el rótulo dice «incompleto»: nadie debe tomar esa columna
# como «el último número de Odoo» (ni como base del libro de kubera).
ODOO_CONGELADO = "15-oct 18:34"
# Las fuentes que NO son Odoo y que existen de verdad. Los lectores (rótulo,
# `kubera_decide`, la resta) deciden con esta lista y no con `!= "odoo"`: una
# errata en la variable hace que la pasada se niegue, pero no mueve a nadie más.
_SIN_ODOO = ("libro",)
# Las acciones de `ops.fanout_log` (canal 'woocommerce') que son «el stock cambió,
# repártelo»: una por fuente. La lista es UNA para que el recuperador, la matriz y
# el panel no se queden contando dos de tres (acuerdo con INVENTARIO FANOUT, 17-oct).
ACCIONES_CAMBIO = ("odoo_delta", "woo_cambio", "vendible_cambio")
# FUENTE=libro: si el libro trae menos de esta fracción de los SKUs que la última
# pasada vio con número, no se le cree (tablas a medias, una bodega que dejó de
# contar): la pasada se frena.
_PISO = 0.9
# FUENTE≠odoo: dos pasadas SEGUIDAS que no repartieron (libro ilegible o parcial,
# foto ilegible, freno, fuente desconocida) avisan por Slack, a lo más una vez por
# hora (el enfriamiento por omisión de `alertas.avisar`). Una sola puede ser un
# parpadeo; dos ya son 40 min sin que un cambio llegue a los canales.
_AVISO_TRAS = 2
_racha_sin_repartir = 0


def habilitado() -> bool:
    return bool(getattr(settings, "stock_watch_enabled", False))


def fuente() -> str:
    """`odoo` | `woo` | `libro` (`STOCK_WATCH_FUENTE`). Sin la variable —o vacía, o
    sólo espacios—, `odoo`. Un valor que no se conoce NO cae a Odoo: `revisar` se
    niega a correr con él (y lo avisa)."""
    return str(getattr(settings, "stock_watch_fuente", "odoo") or "").strip().lower() or "odoo"


def odoo_vivo() -> bool:
    """¿El Odoo de la foto se sigue moviendo? Falso sólo con una fuente conocida que
    no es Odoo (`_SIN_ODOO`); con una errata la pasada no corre, pero los lectores
    siguen como con Odoo."""
    return fuente() not in _SIN_ODOO


def odoo_rotulo() -> str:
    """Cómo se rotula la columna «Odoo» de la foto en el panel (matriz, Bodegas):
    con otra fuente ese número ya no se mueve y nadie debe leerlo como vivo."""
    return "Odoo" if odoo_vivo() else f"Odoo (congelado al {ODOO_CONGELADO}, incompleto)"


def solo_registro() -> bool:
    return bool(getattr(settings, "stock_watch_solo_registro", True))


def tope() -> int:
    return int(getattr(settings, "stock_watch_tope", 300) or 300)


def resta_pendientes() -> bool:
    """¿Se descuentan las piezas vendidas cuya orden todavía no nace en Odoo?

    Sólo tiene sentido con el modo ABSOLUTO encendido: es ahí donde Woo COPIA
    `free_qty`, y por tanto donde una pieza vendida sin reservar reaparece. En
    DELTA la venta ya bajó Woo por su cuenta y restar otra vez la descontaría
    dos veces, así que la bandera se ignora (ver `_deltas_odoo`). Se comprueba
    aquí además de allí para que `estado()` diga la verdad en el panel.

    Y sólo con FUENTE=odoo: con `woo` nada se copia de Odoo, la venta ya bajó Woo
    por su cuenta (igual que en DELTA) y no hay a qué restarle.
    """
    return (bool(getattr(settings, "stock_watch_resta_pendientes", False))
            and bool(getattr(settings, "stock_watch_absoluto", False))
            and odoo_vivo())


def _ventana_pendientes() -> int:
    """Los días que una venta sin orden puede seguir escondiendo su mercancía.

    Es la MISMA ventana en la que el trabajo de guías sigue dispuesto a crear la
    orden, y no una propia: mientras se pueda crear hay que esconder, y en
    cuanto se deja de crear hay que devolver. Dos plazos distintos sólo podrían
    desincronizarse, y los dos lados de esa brecha son un error — resucitar
    mercancía vendida, o esconderla para siempre.

    ⚠️ Y ESO ANTES ERA MENTIRA. Este lado leía `odoo_ventas_espera_guia_dias` y
    el otro usaba el `dias` que el scheduler le pasaba al job
    (`temu_guias_dias` / `tiktok_guias_dias`): tres ajustes distintos que
    coincidían en 14 por omisión. Bajar `TEMU_GUIAS_DIAS` a 7 —una variable de
    rendimiento— dejaba siete días de mercancía escondida sin nadie que creara
    la orden; subirlo a 21 devolvía las piezas al anaquel mientras el job
    todavía crearía la orden, y eso es sobreventa. Se arregló en el otro lado:
    `odoo_ventas.cola_de_guias` usa `_dias_espera()`, que lee esta MISMA
    variable. Ahora hay un solo número; ver `odoo_ventas._dias_espera`.
    """
    return int(getattr(settings, "odoo_ventas_espera_guia_dias", 14) or 14)


def estado() -> dict[str, Any]:
    f = fuente()
    if f == "libro":
        modo = "libro (reparte los cambios del vendible de kubera; ni Woo ni Odoo)"
    elif f != "odoo":
        modo = f"{f} (no existe: no corre)"
    elif getattr(settings, "stock_watch_absoluto", False):
        modo = "absoluto (Odoo master)"
    else:
        modo = "delta (Woo conserva su base)"
    return {**_ultimo, "habilitado": habilitado(),
            "solo_registro": solo_registro(), "tope": tope(),
            "fuente": f, "odoo_rotulo": odoo_rotulo(),
            "odoo_congelado": None if odoo_vivo() else ODOO_CONGELADO, "modo": modo,
            "resta_pendientes": resta_pendientes(),
            "resta_pendientes_pedida": bool(
                getattr(settings, "stock_watch_resta_pendientes", False)),
            "resta_pendientes_dias": _ventana_pendientes(),
            "foto_en_kubera": kubera_escribe(), "foto_decide_kubera": kubera_decide()}


def _asegurar_schema() -> None:
    with db.get_cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {_TABLA} (
                sku          VARCHAR(64)  NOT NULL PRIMARY KEY,
                stock_woo    INT          NULL,
                stock_odoo   INT          NULL,
                actualizado  DATETIME     NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)


def kubera_escribe() -> bool:
    """¿La foto se está guardando TAMBIÉN en kubera? (paso 2, fase 1)"""
    from services import supabase_db as sdb
    return bool(getattr(settings, "supabase_write_stock_watch", False)) and sdb.disponible()


def kubera_decide() -> bool:
    """¿La foto que se LEE —la que decide los deltas— sale ya de kubera?

    Con una fuente que no es Odoo (`woo`, `libro`), siempre: esas pasadas sólo
    escriben `ops.stock_watch_photo`, y el espejo del DROP (`channel_mirror`) sigue
    a esta misma función para no leer una foto de MySQL que ya nadie mueve.

    ⚠️ Por eso `STOCK_WATCH_FUENTE=odoo` NO es la reversa de `woo`: devuelve esta
    decisión a `SUPABASE_READ_STOCK_WATCH` (no está puesta en Railway) y el espejo
    del DROP volvería a la foto de MySQL, parada en la última pasada de Odoo. La
    reversa es `STOCK_WATCH_ENABLED=false` o `STOCK_WATCH_SOLO_REGISTRO=true`."""
    from services import supabase_db as sdb
    return ((bool(getattr(settings, "supabase_read_stock_watch", False)) or not odoo_vivo())
            and sdb.disponible())


def _foto() -> dict[str, dict[str, int | None]]:
    """La foto anterior. NUNCA devuelve `{}` para tapar un error.

    Antes tenía `except Exception: return {}` con el comentario "tabla aún no
    creada" — pero `_asegurar_schema()` corre justo antes, así que ese except ya
    solo atrapaba fallos REALES de la base. Y ahí `{}` no decía "no hay foto",
    decía "no sé": `revisar()` lo tomaba por PRIMERA PASADA, y la primera pasada
    absorbe en la foto todo lo pendiente SIN aplicarlo. Un parpadeo de MySQL
    tiraba a la basura, en silencio, los deltas de Odoo y los cambios de Woo de
    esa vuelta — y con `STOCK_WATCH_SOLO_REGISTRO=false` eso es mercancía que
    nunca llegó a los canales.

    Ahora el error PROPAGA y la pasada se aborta. Vacío de verdad (primera
    corrida) sigue devolviendo `{}`; roto y vacío ya no se confunden.
    """
    if kubera_decide():
        from services import stock_watch_read
        return stock_watch_read.foto_leer()
    return {r["sku"]: {"woo": r["stock_woo"], "odoo": r["stock_odoo"]}
            for r in db.fetch_all(f"SELECT sku, stock_woo, stock_odoo FROM {_TABLA}")}


def _guardar_foto_mysql(filas: list[tuple[str, int | None, int | None]],
                        ahora: datetime | None = None) -> None:
    ahora = (ahora or datetime.now(timezone.utc)).replace(tzinfo=None)
    with db.get_cursor() as cur:
        for i in range(0, len(filas), 500):
            lote = filas[i:i + 500]
            cur.executemany(
                f"""INSERT INTO {_TABLA} (sku, stock_woo, stock_odoo, actualizado)
                    VALUES (%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE stock_woo=VALUES(stock_woo),
                        stock_odoo=VALUES(stock_odoo), actualizado=VALUES(actualizado)""",
                [(s, w, o, ahora) for s, w, o in lote])


def _guardar_foto(filas: list[tuple[str, int | None, int | None]]) -> None:
    """Guarda la foto donde toque según la fase del paso 2.

    El lado que MANDA se escribe de forma síncrona y si falla, falla la pasada:
    perder la foto no es perder un dato, es perder la memoria contra la que se
    calcula el delta de la próxima vuelta. El otro lado es best-effort.
    """
    if not filas:
        return
    # UN solo instante para los dos lados: así el arnés de comparación puede
    # exigir igualdad exacta en vez de una tolerancia inventada.
    ahora = datetime.now(timezone.utc)
    if kubera_decide():
        from services import stock_watch_read
        stock_watch_read.foto_guardar(filas, ahora)   # manda kubera
        try:
            _guardar_foto_mysql(filas, ahora)         # espejo inverso, best-effort
        except Exception as exc:  # noqa: BLE001
            log.warning("stock_watch: espejo inverso a MySQL falló: %s", exc)
        return
    _guardar_foto_mysql(filas, ahora)                 # manda MySQL
    if kubera_escribe():
        try:
            from services import stock_watch_read
            stock_watch_read.foto_guardar(filas, ahora)
        except Exception as exc:  # noqa: BLE001
            log.warning("stock_watch: copia de la foto a kubera falló: %s", exc)


class RestaNoConfiable(RuntimeError):
    """No se pudo MEDIR lo vendido-sin-orden. La pasada no copia Odoo → Woo."""


def _pendientes() -> dict[str, Any]:
    """Piezas ya vendidas cuya orden todavía NO existe en Odoo. ⚠️ BLOQUEA.

    Devuelve `{"pend": {sku: piezas}, "ventas": n, "mas_vieja_h": h,
    "muestra": [...]}`. LANZA `RestaNoConfiable` si no puede responder con
    certeza — y eso es el punto entero de esta función.

    POR QUÉ NO BASTA `piezas_sin_orden`. Ese helper nunca lanza: ante cualquier
    fallo devuelve `{}`, que aquí significaría "no hay nada vendido pendiente" y
    mandaría copiar `free_qty` tal cual — resucitando lo vendido justo cuando la
    base está caída y nadie lo está mirando. `{}` legítimo y `{}` por error son
    indistinguibles desde fuera, y tienen consecuencias opuestas.

    Así que la pregunta se hace DOS veces: primero una consulta CENTINELA que sí
    propaga su error y cuenta los espacios abiertos, y sólo después la que trae
    los números. Si el centinela cuenta ventas esperando y el desglose llega
    vacío, algo se rompió entre una y otra: se falla cerrado. Son dos consultas
    agregadas por pasada (cada 20 min), no una por SKU.

    ⚠️ PERO "VINO VACÍO" TENÍA DOS CAUSAS Y SE TRATABAN IGUAL, y la segunda no
    es transitoria: `registrar` salta las líneas con `cantidad <= 0` o SKU
    vacío pero SÍ escribe el encabezado, así que un encabezado SIN RENGLONES es
    un estado alcanzable y estable. Con una sola venta así —y siendo la única
    que espera— el desglose venía vacío, esto lanzaba, y el tramo Odoo→Woo se
    saltaba ENTERO cada 20 minutos durante los 14 días de la ventana: una
    recepción de mercancía no llegaba nunca a la tienda y un SKU agotado en Odoo
    no bajaba nunca a 0 en Woo. Catorce días sin sincronizar inventario hace más
    daño que la pieza que se intentaba esconder.

    Por eso el centinela cuenta ADEMÁS cuántas de esas ventas tienen renglones
    (`ventas_con_items`, mismo `where`, misma consulta). Si ninguna los tiene,
    un desglose vacío es la respuesta CORRECTA —no hay nada que restar— y la
    pasada sigue con su aviso. Sólo se falla cerrado cuando alguna SÍ tenía
    renglones y aun así no vino nada: eso sólo puede ser un fallo real.

    El centinela aprovecha para traer la ANTIGÜEDAD de la espera más vieja y una
    muestra de números de venta, que es lo que hace legible el log: sin eso, un
    "se restaron 86 piezas" no dice si son de hoy o de una venta muerta de hace
    dos semanas. Números de venta, jamás datos del comprador.
    """
    from services import odoo_ventas_log as oul

    dias = _ventana_pendientes()
    # Propaga su error a propósito (es el centinela): `_pendientes` entera falla
    # cerrado si kubera no contesta.
    c = oul.centinela_pendientes(dias=dias)
    ventas = int(c.get("ventas") or 0)
    horas = float(c.get("mas_vieja_h") or 0)
    muestra = list(c.get("muestra") or [])
    con_items = int(c.get("ventas_con_items") or 0)

    if not ventas:
        return {"pend": {}, "ventas": 0, "mas_vieja_h": 0.0, "muestra": []}

    crudo = oul.piezas_sin_orden(dias=dias)
    if not crudo and con_items:
        # Había ventas CON renglones y el desglose vino vacío: la base se cayó
        # entre las dos consultas. Sin saber cuánto restar, no se copia nada.
        raise RestaNoConfiable(
            f"{ventas} ventas esperando guía ({con_items} con renglones) y el "
            f"desglose por SKU vino vacío")
    if not crudo:
        # Ninguna tenía renglones: no es un fallo de medición, es una venta con
        # las líneas perdidas. Se resta 0 y se SIGUE copiando — pero se dice,
        # porque una venta sin renglones es un dato roto que alguien tiene que
        # mirar, no algo que se pueda dejar pasar en silencio.
        log.warning("stock_watch: %s venta(s) esperando guía y NINGUNA tiene "
                    "renglones en ops.odoo_sale_order_items (%s). No hay nada "
                    "que restar y la copia Odoo→Woo sigue; revisar esas ventas.",
                    ventas, ", ".join(muestra) or "?")
        return {"pend": {}, "ventas": ventas, "mas_vieja_h": round(horas, 1),
                "muestra": muestra, "sin_renglones": ventas}

    pend = {s: int(round(float(v))) for s, v in crudo.items()
            if float(v or 0) > 0}
    return {"pend": pend, "ventas": ventas, "mas_vieja_h": round(horas, 1),
            "muestra": muestra,
            "sin_renglones": max(0, ventas - con_items)}


def _vincular_capturadas() -> dict[str, int]:
    """
    Pega a su orden de Odoo las ventas que alguien capturó A MANO, antes de
    medir los pendientes. ⚠️ BLOQUEA: se llama con `asyncio.to_thread`.

    Sólo para los canales que de verdad están en régimen diferido — es el único
    caso en que la bitácora manda sobre el inventario. Nunca lanza hacia fuera
    (el que llama lo atrapa igual) y no escribe en Odoo: `vincular_sin_orden`
    sólo LEE Odoo y escribe la bitácora.
    """
    from services import odoo_ventas
    fuera: dict[str, int] = {}
    for canal in sorted(odoo_ventas.canales_posibles()):
        try:
            if not odoo_ventas.espera_guia_activa(canal):
                continue
            from services import odoo_ventas_log
            n = int((odoo_ventas_log.vincular_sin_orden(canal, dias=30)
                     or {}).get("vinculadas") or 0)
            if n:
                fuera[canal] = n
                log.info("stock_watch: %s orden(es) de %s capturadas a mano se "
                         "vincularon antes de medir pendientes", n, canal)
        except Exception as exc:  # noqa: BLE001 — una mala no detiene las demás
            log.debug("_vincular_capturadas(%s): %s", canal, exc)
    return fuera


def _anotar(sku: str, accion: str, motivo: str, resultado: str, mysql: bool = True,
            dry_run: bool | None = None) -> None:
    """Bitácora en `fanout_log` (la que ya pinta el Dashboard).

    `mysql=False` (FUENTE≠odoo): sólo `ops.fanout_log` en kubera, sin la copia
    local de MySQL ni su `_asegurar_schema`. `dry_run` None → `solo_registro()`;
    la pasada del libro lo pasa explícito (también registra cuando el fan-out
    todavía lee Woo)."""
    from services import fanout_read
    _ahora = datetime.now(timezone.utc).replace(tzinfo=None)
    fanout_read.espejar(ts=_ahora, sku=sku[:64], motivo=motivo[:255],
                        dry_run=bool(solo_registro() if dry_run is None else dry_run),
                        canal="woocommerce",
                        cuenta="ODOO", accion=accion, resultado=resultado[:255],
                        ms=0)
    if not mysql:
        return
    try:
        from services import fanout_stock
        fanout_stock._asegurar_schema()
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO fanout_log
                   (ts, sku, motivo, dry_run, stock_drop, objetivo, canal, cuenta,
                    item_id, accion, stock_canal, resultado, ms)
                   VALUES (%s,%s,%s,%s,NULL,NULL,%s,%s,NULL,%s,NULL,%s,0)""",
                (_ahora, sku[:64], motivo[:255],
                 1 if solo_registro() else 0, "woocommerce", "ODOO", accion, resultado[:255]))
    except Exception as exc:  # noqa: BLE001
        log.warning("stock_watch: no se pudo anotar %s: %s", sku, exc)


class LecturaWooIncompleta(RuntimeError):
    """Woo contestó menos de lo que tiene (o nada). La comparación de T0 no decide con eso."""


def _leer_woo(estricto: bool = False) -> dict[str, dict[str, Any]]:
    """Stock actual de TODO el catálogo de Woo (una sola consulta).

    `estricto=True` (la comparación de T0, `--woo vivo`) cuenta ANTES las filas que la consulta debe
    traer y LANZA `LecturaWooIncompleta` si llegan menos, o ninguna: un Woo vacío
    no es «todo en cero». Un producto que se manda a la papelera entre las dos
    consultas da un falso aborto — inofensivo: la pasada siguiente lo vuelve a
    leer. Sin `estricto` es la consulta de siempre."""
    from services import wp_db
    P = wp_db._prefix()
    desde = f"""FROM {P}postmeta sk
            JOIN {P}posts p ON p.ID = sk.post_id
                 AND p.post_type IN ('product','product_variation')
                 AND p.post_status <> 'trash'
            LEFT JOIN {P}postmeta st ON st.post_id = p.ID AND st.meta_key = '_stock'
            LEFT JOIN (SELECT tr.object_id, t.slug
                         FROM {P}term_relationships tr
                         JOIN {P}term_taxonomy tt ON tt.term_taxonomy_id = tr.term_taxonomy_id
                              AND tt.taxonomy = 'product_type'
                         JOIN {P}terms t ON t.term_id = tt.term_id) pt ON pt.object_id = p.ID
            WHERE sk.meta_key = '_sku' AND sk.meta_value <> ''"""
    esperadas = (int((wp_db._fetch_all(f"SELECT COUNT(*) n {desde}") or [{}])[0].get("n") or 0)
                 if estricto else 0)
    filas = wp_db._fetch_all(
        f"""SELECT sk.meta_value sku, st.meta_value stock, p.ID, p.post_type, p.post_parent,
                   pt.slug ptype
            {desde}""")
    if estricto and (not filas or len(filas) < esperadas):
        raise LecturaWooIncompleta(f"Woo devolvió {len(filas)} de {esperadas} filas")
    fuera: dict[str, dict[str, Any]] = {}
    for r in filas:
        s = (r["sku"] or "").strip()
        if not s:
            continue
        try:
            v = int(float(r["stock"])) if r["stock"] not in (None, "") else None
        except (TypeError, ValueError):
            v = None
        fuera[s] = {"stock": v, "id": r["ID"], "tipo": r["post_type"],
                    "padre": r["post_parent"], "variable": r.get("ptype") == "variable"}
    return fuera


async def _escribir_woo(cambios: list[tuple[str, int, dict]]) -> tuple[int, set[str]]:
    """Aplica los destinos calculados a Woo por REST (nunca DML sobre wp_*).

    Devuelve (cuántos se escribieron, QUÉ SKUs quedaron escritos OK). El
    conjunto importa tanto como el conteo: la foto solo debe absorber los SKUs
    realmente escritos — absorber un fallo pierde el delta de Odoo PARA SIEMPRE
    y en silencio (pasó de verdad: ORG-0785 se quedó sin 60 pzas y TEC-0965 sin
    14 porque el batch falló y la foto dio el destino por hecho).

    Woo responde 200 al batch aunque un ítem individual truene: cada elemento
    de `update` puede traer su propio `error`. Por eso se revisa ítem por ítem
    y no solo el status del lote.
    """
    from services import woocommerce
    simples, variaciones = [], {}
    id_a_sku = {int(w["id"]): sku for sku, _d, w in cambios}
    for _sku, destino, w in cambios:
        upd = {"id": w["id"], "manage_stock": True, "stock_quantity": destino}
        if w["tipo"] == "product_variation" and w["padre"]:
            variaciones.setdefault(w["padre"], []).append(upd)
        else:
            simples.append(upd)

    def _oks(respuesta: Any, lote: list[dict]) -> set[str]:
        """SKUs OK de un batch: sin campo `error` en su elemento de respuesta."""
        try:
            filas = (respuesta or {}).get("update") or []
            con_error = {int(f["id"]) for f in filas
                         if f.get("id") is not None and f.get("error")}
            return {id_a_sku[int(u["id"])] for u in lote
                    if int(u["id"]) in id_a_sku and int(u["id"]) not in con_error}
        except Exception:  # noqa: BLE001 — respuesta ilegible: se asume el lote OK
            return {id_a_sku[int(u["id"])] for u in lote if int(u["id"]) in id_a_sku}

    hechos, ok_skus = 0, set()
    async with woocommerce._client() as cli:
        for i in range(0, len(simples), 50):
            lote = simples[i:i + 50]
            try:
                r = await cli.post("/products/batch", json={"update": lote}, timeout=300.0)
                if r.status_code in (200, 201):
                    escritos = _oks(r.json(), lote)
                    ok_skus |= escritos
                    hechos += len(escritos)
            except Exception as exc:  # noqa: BLE001
                log.warning("stock_watch: batch simples falló: %s", exc)
            await asyncio.sleep(0.5)
        for padre, lote in variaciones.items():
            try:
                r = await cli.post(f"/products/{padre}/variations/batch",
                                   json={"update": lote}, timeout=300.0)
                if r.status_code in (200, 201):
                    escritos = _oks(r.json(), lote)
                    ok_skus |= escritos
                    hechos += len(escritos)
            except Exception as exc:  # noqa: BLE001
                log.warning("stock_watch: batch variaciones de %s falló: %s", padre, exc)
            await asyncio.sleep(0.5)
    return hechos, ok_skus


def _deltas_odoo(od: dict[str, int], wo: dict[str, dict[str, Any]],
                 foto: dict[str, dict[str, int | None]],
                 absoluto: bool,
                 pend: dict[str, int] | None = None) -> list[tuple[str, int, dict]]:
    """Los destinos Odoo -> Woo de una pasada: [(sku, destino, fila de Woo)].

    WOO SIN NÚMERO (`_stock` vacío) es "Gestionar inventario" apagado, y así
    nace todo lo que da de alta Crear productos. Saltarlo siempre lo dejaba
    fuera para siempre: el candado de publicar leía el hueco como 0 y culpaba a
    Odoo ("Sin stock en Odoo"). El 23-sep eran 36 simples con 4,564 piezas
    libres que no se podían publicar (TEC-2370-MET: 60).

    Ahora se le prende y se le copia Odoo, pero con tres límites:
      · solo en modo ABSOLUTO: el delta no tiene una base de la cual partir;
      · nunca a un padre `variable`: el stock lo llevan sus variaciones;
      · solo si Odoo TIENE piezas (opción «a» de Eduardo, 23-sep). Prenderla
        con 0 pasaría a "agotado" productos que Woo hoy ofrece como
        disponibles; esos se revisan a mano, no los decide el vigilante.

    `pend` son las piezas YA VENDIDAS cuya orden todavía no nace en Odoo, y se
    restan de `free_qty` antes de copiar. **Sólo se aplican en ABSOLUTO**, y eso
    es una salvaguarda, no una omisión: en DELTA la venta ya bajó Woo por su
    cuenta (`PEDIDOS_WC_DESCUENTA_STOCK=true` desde el día 1), así que restarlas
    otra vez descontaría la misma pieza dos veces. Por eso la condición vive en
    el código y no sólo en el comentario de la bandera.

    La resta se aplica también al caso de "Woo sin número": prender "Gestionar
    inventario" con el `free_qty` crudo publicaría como disponible una pieza que
    ya se vendió. Si al restar no queda nada, no se prende — que es la misma
    prudencia de la regla de arriba: el vigilante no manda a "agotado" lo que
    Woo hoy ofrece; eso lo revisa una persona.
    """
    pend = pend or {}
    deltas: list[tuple[str, int, dict]] = []
    for sku, ahora_od in od.items():
        w = wo.get(sku)
        if w is None:
            continue
        # En DELTA nunca se resta — y hay TRES cosas sosteniéndolo, porque el
        # día que se caiga una nadie se va a enterar mirando este renglón:
        #   1) `resta_pendientes()` exige el modo absoluto, así que en delta
        #      `pend` ni siquiera se consulta y llega vacío;
        #   2) este `if absoluto`;
        #   3) `libre` sólo se CONSUME en las dos ramas de absoluto — la rama
        #      del delta calcula su destino aparte, a partir de la foto.
        # Lo que hay que cuidar al tocar esto es (3): restar `pend` dentro de la
        # fórmula del delta descontaría dos veces la misma pieza, porque la
        # venta ya bajó Woo por su cuenta.
        # `max(0, …)` porque los pendientes pueden superar a `free_qty` (una
        # venta que Odoo todavía no conoce), y ahí lo correcto es cero.
        libre = max(0, ahora_od - pend.get(sku, 0)) if absoluto else ahora_od
        if w["stock"] is None:
            if absoluto and libre > 0 and not w.get("variable"):
                deltas.append((sku, libre, w))
            continue
        if absoluto:
            # `od` ya viene con max(0, …) aplicado arriba.
            destino = libre
        else:
            ant = foto.get(sku, {}).get("odoo")
            if ant is None or ahora_od == ant:
                continue
            destino = max(0, w["stock"] + (ahora_od - ant))
        if destino != w["stock"]:
            deltas.append((sku, destino, w))
    return deltas


def _cambios_woo(wo: dict[str, dict[str, Any]],
                 foto: dict[str, dict[str, int | None]]) -> list[tuple[str, int, int]]:
    """[(sku, antes, ahora)]: el `_stock` de Woo que se movió contra la foto. Un
    número contra otro número; un SKU sin foto o sin número no es un cambio (la
    foto lo absorbe y su próximo movimiento ya se ve)."""
    movidos: list[tuple[str, int, int]] = []
    for sku, w in wo.items():
        ant = foto.get(sku, {}).get("woo")
        if ant is not None and w["stock"] is not None and w["stock"] != ant:
            movidos.append((sku, ant, w["stock"]))
    return movidos


async def revisar(forzar: bool = False) -> dict[str, Any]:
    """Una pasada completa. La corre el scheduler; también sirve a mano.

    `forzar` se salta el tope y el piso, pero NO el apagado: con
    `STOCK_WATCH_ENABLED=false` sólo corre una pasada forzada que no puede mover
    nada (`STOCK_WATCH_SOLO_REGISTRO=true`). Apagarlo (el acta P2) no se deshace
    con un botón."""
    if not habilitado() and not (forzar and solo_registro()):
        return {"estado": "apagado"}
    if _lock.locked():
        return {"estado": "ya_corriendo"}

    async with _lock:
        global _pasada_desde
        t0 = _pasada_desde = time.time()
        # ── LA FUENTE: cada una en su propia función, antes de tocar MySQL ──
        f = fuente()
        if f == "libro":
            return await _pasada_libro(forzar, t0)
        if f != "odoo":
            return await _fuente_sin_construir(f)
        await asyncio.to_thread(_asegurar_schema)
        from services import odoo, fanout_stock

        # ── LO VENDIDO SIN ORDEN, **ANTES** DE PREGUNTARLE A ODOO ─────────
        #
        # El orden de estas dos lecturas es la única defensa contra el CRUCE:
        # el instante en que la orden por fin nace en Odoo y la reserva real
        # toma el relevo de esta resta. Las dos fuentes son sistemas distintos
        # y no se pueden leer a la vez, así que siempre habrá una ventana; lo
        # que se elige es HACIA DÓNDE se equivoca.
        #
        #   pendientes → Odoo  (lo que se hace): si la orden nace en medio, se
        #     ve la reserva nueva Y los pendientes viejos. Se resta de más por
        #     UNA pasada: la pieza queda escondida ≤20 min y la vuelta
        #     siguiente la devuelve sola, porque la fila ya salió de la cola.
        #
        #   Odoo → pendientes (lo contrario): se ve el `free_qty` de ANTES de
        #     la reserva y los pendientes ya en cero. Se ofrece de más: la
        #     pieza vendida vuelve al anaquel y al fan-out — exactamente el
        #     problema que esta parte viene a resolver.
        #
        # Entre esconder 20 minutos y resucitar, se esconde. Medido en
        # `scratchpad/test_stock_pendientes.py` ("el cruce", los dos sentidos).
        #
        # LO QUE CUESTA, para que nadie lo reporte como un bug: cada cruce son
        # DOS empujes al fan-out en 20 minutos. La pasada del cruce baja Woo y
        # encola el SKU; la siguiente lo sube de vuelta y lo encola otra vez. El
        # anaquel de Temu/TikTok hace 204→191→204 sin que nada haya cambiado de
        # verdad. Es barato y se auto-sana: la ventana real es el hueco entre
        # `action_confirm` y el `registrar` que le sigue —segundos— sobre un
        # ciclo de 1,200 s. No se corrige porque la alternativa (releer los
        # pendientes antes de bajar) añade una consulta por pasada para ahorrar
        # un parpadeo que ya va en la dirección segura.
        #
        # ── Y ANTES DE LEER LOS PENDIENTES, VINCULAR LO QUE SE CAPTURÓ A MANO ─
        #
        # `piezas_sin_orden` decide por `odoo_order_id is null` EN LA BITÁCORA,
        # que no le pregunta a Odoo. Cuando Gabriela crea y confirma la orden
        # directamente en Odoo, Odoo reserva al INSTANTE (`free_qty` baja) pero
        # la bitácora no se entera hasta que corre `vincular_sin_orden` (cada 15
        # min). En ese hueco esta pasada resta otra vez lo que Odoo ya reservó:
        # doble descuento, y el propio panel es lo que invita a capturarla.
        #
        # Se auto-sana y el error va hacia esconder (no hay sobreventa), pero en
        # los cuatro SKUs sin holgura medidos deja el anaquel en 0 y publica el
        # agotado en los canales DROP. Así que la lectura de la bitácora se hace
        # DESPUÉS de vincular, no antes: el hueco deja de ser estructural.
        # Nunca rompe la pasada — si Odoo no contesta se sigue como antes.
        if resta_pendientes():
            try:
                await asyncio.to_thread(_vincular_capturadas)
            except Exception as exc:  # noqa: BLE001
                log.warning("stock_watch: no se pudo vincular lo capturado a "
                            "mano antes de leer pendientes (%s); se sigue", exc)

        pend: dict[str, int] = {}
        pend_info: dict[str, Any] = {}
        if resta_pendientes():
            try:
                pend_info = await asyncio.to_thread(_pendientes)
                pend = pend_info.get("pend") or {}
            except Exception as exc:  # noqa: BLE001
                # FALLA CERRADO. No se sabe cuánto hay vendido sin surtir, así
                # que no se copia NADA de Odoo a Woo: mejor que Woo se quede
                # como está —desactualizado— a que copie un `free_qty` que
                # todavía cuenta piezas ya vendidas. El tramo Woo→canales sigue
                # corriendo más abajo: replica lo que Woo YA dice y por tanto no
                # puede resucitar nada que Woo no estuviera ofreciendo.
                msg = (f"No se pudo medir lo vendido-sin-orden ({exc}). "
                       f"NO se copió nada de Odoo a Woo en esta pasada; el "
                       f"tramo Woo→canales sí corrió.")
                log.error("stock_watch: %s", msg)
                await asyncio.to_thread(_anotar, "*", "stock_watch_freno",
                                        "pendientes no medibles", msg)
                pend_info = {"error": str(exc)[:200]}
                pend = {}
                resta_ciega = True
            else:
                resta_ciega = False
        else:
            resta_ciega = False

        try:
            catalogo = await asyncio.to_thread(odoo.listar_catalogo, estricto=True)
        except odoo.CatalogoIncompleto as exc:
            # Odoo cortó A MEDIA lectura (15-oct 18:34: negó el acceso en el
            # offset 5500). Un catálogo parcial no es el catálogo: copiarlo o
            # guardarlo en la foto borra el `stock_odoo` del resto. Se aborta.
            log.error("stock_watch: lectura de Odoo incompleta, pasada abortada: %s", exc)
            _ultimo.update(estado="odoo_lectura_parcial", ts=time.time(),
                           nota=f"{exc}. Pasada abortada; nada se escribió.")
            return dict(_ultimo)
        if not catalogo:
            # Odoo mudo: NO se interpreta como "todo en cero" (eso vaciaría el
            # catálogo entero). Se aborta la pasada.
            _ultimo.update(estado="odoo_sin_respuesta", ts=time.time())
            return dict(_ultimo)
        od = {(p.get("sku") or "").strip(): max(0, int(float(p.get("stock") or 0)))
              for p in catalogo if (p.get("sku") or "").strip()}
        wo = await asyncio.to_thread(_leer_woo)
        try:
            foto = await asyncio.to_thread(_foto)
        except Exception as exc:  # noqa: BLE001
            # Se aborta igual que con Odoo mudo, y por la misma razón: sin la
            # foto anterior no hay delta que calcular, y seguir significaría
            # tratar "no sé" como "no había nada". Ver `_foto`.
            log.error("stock_watch: no se pudo leer la foto anterior: %s", exc)
            _ultimo.update(estado="foto_no_disponible", ts=time.time(),
                           nota=f"No se pudo leer la foto anterior ({exc}). "
                                f"Pasada abortada; nada se escribió.")
            return dict(_ultimo)

        # ── PRIMERA PASADA: solo levantar la base, nunca escribir ──────────
        if not foto:
            filas = [(s, w["stock"], od.get(s)) for s, w in wo.items()]
            for s in od:
                if s not in wo:
                    filas.append((s, None, od[s]))
            await asyncio.to_thread(_guardar_foto, filas)
            _ultimo.update(estado="foto_base", ts=time.time(), skus=len(filas),
                           segundos=round(time.time() - t0, 1),
                           nota="Primera pasada: solo se guardó la foto base. No se escribió nada.")
            log.info("stock_watch: foto base levantada con %d SKUs (sin escribir)", len(filas))
            return dict(_ultimo)

        # ── 1) ODOO → Woo ─────────────────────────────────────────────────
        #
        # DOS MODOS, y el correcto depende de QUIÉN es la fuente de verdad:
        #
        # ABSOLUTO (`STOCK_WATCH_ABSOLUTO=true`, desde el 28-ago): Woo COPIA el
        #   `free_qty` de Odoo. Es el modo que corresponde a "Odoo es el master"
        #   (decisión de Brandon, 20-ago). Corrige la diferencia venga de donde
        #   venga y respeta las RESERVAS, que es lo que el delta no podía ver.
        #
        # DELTA (el modo viejo): Woo conserva su propio absoluto y Odoo solo
        #   aporta su variación. Era lo correcto cuando Woo mandaba —mandar el
        #   absoluto habría resucitado mercancía vendida— pero arrastra para
        #   siempre la diferencia de base: medido el 27-ago, JAR-0031-NEG bajó
        #   12 en Odoo y 12 en Woo (delta perfecto) y aun así quedó Odoo 0 vs
        #   Woo 23, porque venían de bases distintas. Además `free_qty` descuenta
        #   lo reservado y el delta no: VIA-0024-NEG tenía 30 piezas físicas con
        #   29 comprometidas en órdenes, o sea 1 vendible, y Woo ofrecía 14.
        #
        # El modo se cambia con una variable, sin deploy: si el absoluto resulta
        # equivocado, se vuelve al delta en un minuto.
        absoluto = bool(getattr(settings, "stock_watch_absoluto", False))
        # `resta_ciega` es el fallo cerrado de más arriba: se sabe que hay algo
        # vendido sin surtir pero no cuánto, así que el tramo Odoo→Woo se
        # cancela ENTERO. No es lo mismo que `pend = {}` (que significa "no hay
        # nada pendiente" y sí deja copiar).
        deltas = [] if resta_ciega else _deltas_odoo(od, wo, foto, absoluto, pend)

        # ── 2) CAMBIOS DE WOO (venga de donde venga) → canales ────────────
        movidos_woo = _cambios_woo(wo, foto)

        total = len(deltas) + len(movidos_woo)
        # ── CORTACIRCUITOS ────────────────────────────────────────────────
        if total > tope() and not forzar:
            msg = (f"{total} cambios en una pasada (tope {tope()}). NO se aplicó nada. "
                   f"Odoo={len(deltas)} Woo={len(movidos_woo)}. Revisar y forzar si es real.")
            log.error("stock_watch: %s", msg)
            await asyncio.to_thread(_anotar, "*", "stock_watch_freno", "cortacircuitos", msg)
            _ultimo.update(estado="frenado", ts=time.time(), cambios=total,
                           odoo_deltas=len(deltas), woo_cambios=len(movidos_woo), nota=msg)
            return dict(_ultimo)

        # ── APLICAR ───────────────────────────────────────────────────────
        escritos, deltas_ok = 0, set()
        if deltas and not solo_registro():
            escritos, deltas_ok = await _escribir_woo(deltas)
        fallidos = ({s for s, _d, _w in deltas} - deltas_ok) if not solo_registro() else set()
        for sku, destino, w in deltas[:200]:
            fallo = sku in fallidos
            # El motivo dice el `free_qty` CRUDO y, aparte, cuántas piezas se le
            # restaron: sin las dos cifras no se puede reconstruir después por
            # qué Woo quedó donde quedó. El desglose venta por venta no cabe
            # aquí (esta consulta viene agregada por SKU) y vive en el panel,
            # en `odoo_ventas_log.pendientes_sin_orden`.
            restado = pend.get(sku, 0)
            await asyncio.to_thread(
                _anotar, sku, "odoo_delta" if not solo_registro() else "odoo_delta_registro",
                f"delta de Odoo (foto {foto.get(sku, {}).get('odoo')} -> {od[sku]})"
                + (f" − {restado} vendidas sin orden" if restado else ""),
                f"Woo {w['stock']} -> {destino}"
                + (" (ESCRITURA FALLÓ — se reintenta la próxima pasada)" if fallo else ""))

        encolados = 0
        for sku, ant, ahora_wo in movidos_woo:
            await asyncio.to_thread(
                _anotar, sku, "woo_cambio" if not solo_registro() else "woo_cambio_registro",
                "cambio de stock en Woo", f"{ant} -> {ahora_wo} (replicar a canales)")
            if not solo_registro():
                fanout_stock.encolar(sku, motivo="cambio de stock en Woo")
                encolados += 1
        # DELTAS DE ODOO → CANALES (cierre del tramo prometido en la cabecera).
        # Hasta v0.206 los deltas aplicados a Woo NO se encolaban y la foto los
        # absorbía: la siguiente pasada veía Woo == foto y los canales jamás se
        # enteraban (medido: 48 de 755 llegaron, y solo porque una venta
        # concurrente encoló el mismo SKU). El peor caso era revivir de 0 — un
        # canal en 0 no vende, y sin venta no había disparo. Ej. real: PAS-0018
        # 130→0 el 18-ago, invisible para TikTok. Se encola SOLO lo escrito OK:
        # el fan-out relee Woo en vivo, así que empuja el valor ya aplicado.
        for sku in sorted(deltas_ok):
            fanout_stock.encolar(sku, motivo="delta de Odoo aplicado")
            encolados += 1

        # ── GUARDAR FOTO ──────────────────────────────────────────────────
        # Aplicado: la foto del SKU tocado es su destino y Odoo queda absorbido
        # — pero SOLO si su escritura fue OK. Un delta cuya escritura FALLÓ
        # conserva la foto vieja EN LOS DOS LADOS (woo y odoo): así la próxima
        # pasada recalcula el mismo delta y lo reintenta. Antes se absorbía
        # todo, hasta lo fallido, y el delta se perdía para siempre y en
        # silencio (ORG-0785: 60 pzas; TEC-0965: +14).
        #
        # SOLO REGISTRO: la foto NO absorbe lo pendiente. Si absorbiera, un delta
        # de Odoo observado en modo registro desaparecería (la siguiente pasada
        # calcularía delta 0) y al pasar a modo vivo esas piezas ya no se
        # aplicarían NUNCA. Conservando el valor viejo, lo pendiente sigue
        # pendiente y la transición registro → vivo no pierde nada. El costo es
        # que la bitácora repite la propuesta cada pasada, que es la verdad:
        # sigue sin aplicarse.
        pend_odoo = {s for s, _, _ in deltas}
        pend_woo = {s for s, _, _ in movidos_woo}
        destinos = {s: d for s, d, _ in deltas
                    if s in deltas_ok} if not solo_registro() else {}
        filas = []
        for s, w in wo.items():
            if solo_registro():
                v_woo = foto[s]["woo"] if s in pend_woo and s in foto else w["stock"]
                v_odoo = foto[s]["odoo"] if s in pend_odoo and s in foto else od.get(s)
            elif s in fallidos and s in foto:
                # Escritura fallida: se conserva la memoria para reintentar.
                v_woo, v_odoo = w["stock"], foto[s]["odoo"]
            else:
                v_woo, v_odoo = destinos.get(s, w["stock"]), od.get(s)
            filas.append((s, v_woo, v_odoo))
        for s in od:
            if s not in wo:
                filas.append((s, None, od[s]))
        await asyncio.to_thread(_guardar_foto, filas)

        # Sólo se CUENTAN las piezas que de verdad cambiaron un destino: `pend`
        # trae todo lo vendido-sin-orden, pero un SKU que Odoo ni siquiera lista
        # no resta nada, y decir "86 restadas" cuando 20 no se aplicaron sería
        # exactamente el tipo de número que después nadie logra cuadrar.
        aplicados = {s: pend[s] for s, _d, _w in deltas if s in pend}
        huerfanos = sorted(set(pend) - set(od))
        # `cambios` sólo lo pone el freno: no debe quedarse pegado en las pasadas buenas.
        _ultimo.pop("cambios", None)
        _ultimo.update(
            estado="ok", ts=time.time(), segundos=round(time.time() - t0, 1),
            skus_odoo=len(od), skus_woo=len(wo),
            odoo_deltas=len(deltas), woo_escritos=escritos,
            woo_cambios=len(movidos_woo), encolados=encolados,
            solo_registro=solo_registro(),
            resta_pendientes=resta_pendientes(),
            resta_ciega=resta_ciega,
            pendientes_ventas=int(pend_info.get("ventas") or 0),
            pendientes_espera_mas_vieja_h=float(pend_info.get("mas_vieja_h") or 0),
            pendientes_skus=len(aplicados),
            pendientes_piezas=sum(aplicados.values()),
            pendientes_ventas_muestra=list(pend_info.get("muestra") or [])[:10],
            pendientes_sin_sku_en_odoo=huerfanos[:10],
            pendientes_detalle=[f"{s}: −{n}" for s, n in
                                sorted(aplicados.items(), key=lambda kv: -kv[1])[:10]],
            muestra=[f"{s}: Woo {w['stock']}→{d}" for s, d, w in deltas[:6]]
                    + [f"{s}: Woo {a}→{b} → canales" for s, a, b in movidos_woo[:6]])
        if resta_ciega:
            _ultimo["nota"] = ("No se pudo medir lo vendido-sin-orden: el tramo "
                               "Odoo→Woo se saltó entero (falla cerrado). "
                               + str(pend_info.get("error") or ""))
        else:
            # `_ultimo` es un dict que se ACTUALIZA, no se reemplaza: sin esto,
            # la nota de una pasada frenada se quedaría pegada en el panel
            # durante las pasadas buenas que vinieran después.
            _ultimo.pop("nota", None)
        if aplicados:
            log.info("stock_watch: %d pzas de %d SKUs restadas por %d ventas sin "
                     "orden en Odoo (la más vieja lleva %.1f h): %s",
                     sum(aplicados.values()), len(aplicados),
                     int(pend_info.get("ventas") or 0),
                     float(pend_info.get("mas_vieja_h") or 0),
                     ", ".join(f"{s} −{n}" for s, n in
                               sorted(aplicados.items(), key=lambda kv: -kv[1])[:8]))
        if huerfanos:
            log.warning("stock_watch: %d SKUs vendidos-sin-orden no están en el "
                        "catálogo de Odoo, no se les pudo restar nada: %s",
                        len(huerfanos), ", ".join(huerfanos[:8]))
        if total:
            log.info("stock_watch: %d deltas de Odoo (%d escritos) · %d cambios de Woo "
                     "(%d encolados)%s", len(deltas), escritos, len(movidos_woo), encolados,
                     " [SOLO REGISTRO]" if solo_registro() else "")
        return dict(_ultimo)


async def _sin_repartir(estado_: str, msg: str, fuente_: str) -> dict[str, Any]:
    """Una pasada con FUENTE≠odoo que termina SIN repartir (Woo mudo o parcial, foto
    ilegible, freno, bitácora apagada, libro ilegible, fuente desconocida): log, estado y, desde
    la segunda SEGUIDA (`_AVISO_TRAS`), aviso por Slack (`alertas.avisar`, a lo más
    uno por hora). Sólo con el log es justo como falló Odoo el 15-oct: cada pasada
    abortó sin ruido desde las 18:34 y la foto pasó 3 h quieta sin que nadie lo
    supiera. La racha vuelve a cero con la primera pasada que sí reparte."""
    global _racha_sin_repartir
    _racha_sin_repartir += 1
    log.error("stock_watch[%s]: %s", fuente_, msg)
    _ultimo.update(estado=estado_, ts=time.time(), fuente=fuente_, nota=msg,
                   seguidas_sin_repartir=_racha_sin_repartir)
    if _racha_sin_repartir >= _AVISO_TRAS:
        from services import alertas
        # Regla 11: el candado del aviso lee y escribe su tabla → a un hilo. Nunca lanza.
        await asyncio.to_thread(
            alertas.avisar, "stock_watch_sin_repartir",
            f"*stock_watch ({fuente_})*: {_racha_sin_repartir} pasadas seguidas sin repartir los "
            f"cambios de Woo a los canales. Última: `{estado_}` — {msg}")
    return dict(_ultimo)


async def _fuente_sin_construir(f: str) -> dict[str, Any]:
    """Un valor que no se conoce (una errata): la pasada se NIEGA y no toca nada
    —ni Odoo, ni Woo, ni la foto, ni el fan-out—; lo dice en el log, en el estado
    y, si se repite, por Slack."""
    msg = (f"STOCK_WATCH_FUENTE={f!r} no existe (odoo | woo | libro): la pasada no hizo "
           f"nada. Corregir la variable (o apagar STOCK_WATCH_ENABLED).")
    return await _sin_repartir("fuente_no_disponible", msg, f)


def _txt(v: int | None) -> str:
    """Un número en la bitácora: «sin número» cuando no lo hay."""
    return "sin número" if v is None else str(v)


def _movidos_libro(libro: dict[str, int], foto: dict[str, dict[str, Any]]
                   ) -> tuple[list[tuple[str, int | None, int]], set[str]]:
    """FUENTE=libro: lo que se movió en el vendible contra la foto → `(cambios, fuera)`.

    · `cambios` [(sku, antes, ahora)]: el vendible del libro es distinto de `antes`.
      `antes` es el `stock_kubera` de la foto; si el libro nunca había dado número
      para ese SKU (la primera pasada tras T0, o un SKU que entra al libro), el
      último `stock_woo`: lo que el fan-out repartió hasta T0. Así la primera
      pasada encola justo los SKUs en que el libro no coincide con Woo, y el tope
      la frena si son muchos: forzarla es un acto deliberado, después de la
      comparación de T0 (`scripts/comparar_libro_vs_woo.py`).
    · `fuera` {sku}: TODO SKU de la foto que el libro no trae —el que salió (su
      bodega dejó de contar, el renglón desapareció) y el que nunca estuvo—. Lo que
      el libro no trae vale 0 (D4) y su `stock_kubera` se SIEMBRA en 0: así el
      seguro y los excedentes, que leen esa columna, también lo ven. Entra a
      `cambios` como (sku, antes, 0) cuando `antes` —`stock_kubera`, o el último
      `stock_woo` si nunca tuvo— era un número distinto de 0: el canal puede tener
      piezas que ya no hay. Si `antes` era 0 o nada, sólo se siembra: el canal no
      tenía de dónde sacar un número (el seguro, la alineación de T0 y el
      recuperador cubren lo demás).

    Sin distinguir mayúsculas: la llave de la foto es `citext` y el libro ya viene
    en mayúsculas.
    """
    por_mayus = {str(s).upper(): v for s, v in foto.items()}
    cambios: list[tuple[str, int | None, int]] = []
    for sku, ahora in libro.items():
        f = por_mayus.get(sku.upper()) or {}
        ant = f.get("kubera") if f.get("kubera") is not None else f.get("woo")
        if ahora != ant:
            cambios.append((sku, ant, ahora))
    en_libro = {s.upper() for s in libro}
    fuera: set[str] = set()
    for s, v in foto.items():
        k = str(s).upper()
        if k in en_libro:
            continue
        fuera.add(k)
        ant = v.get("kubera") if v.get("kubera") is not None else v.get("woo")
        if ant is not None and ant != 0:
            cambios.append((k, ant, 0))
    return cambios, fuera


def _vistos_con_piezas(foto: dict[str, dict[str, Any]]) -> list[str]:
    """Los SKUs que la ÚLTIMA pasada del libro vio CON PIEZAS (`stock_kubera > 0` y el
    `actualizado` más reciente). El piso del libro se mide con piezas y no con
    «número»: la pasada siembra 0 a todo lo de la foto que el libro no trae, así
    que «con número» sería casi toda la foto y frenaría cada pasada."""
    ultima = max((v["act"] for v in foto.values() if v.get("act") is not None), default=None)
    return [s for s, v in foto.items()
            if (v.get("kubera") or 0) > 0 and (ultima is None or v.get("act") == ultima)]


async def _pasada_libro(forzar: bool, t0: float) -> dict[str, Any]:
    """FUENTE=libro: reparte a los canales los cambios del VENDIBLE del libro de
    kubera (`stock_libro.todos()`: Σ libre_vendible − ventas DROP sin OV). Corre
    bajo `_lock`. Es el segundo tramo de la pasada de Odoo con otra lectura: sin
    Odoo, sin MySQL y sin Woo —ni para escribir ni para leer—. Qué cuenta como cambio: `_movidos_libro`.

    Cada cambio deja una fila `vendible_cambio` (motivo «vendible X -> Y (libro)»,
    resultado «X -> Y (replicar a canales)», `ACCIONES_CAMBIO`) y se encola con el
    motivo «cambio de vendible en kubera». SÓLO se encola si el fan-out lee el libro
    (`FANOUT_FUENTE=kubera`): con el fan-out todavía en Woo, encolar repartiría el
    número de Woo por un cambio del libro. Con el fan-out en Woo, o con
    `STOCK_WATCH_SOLO_REGISTRO`, la pasada sólo registra (`vendible_registro`)
    y lo no repartido conserva su número viejo en la foto: sigue pendiente al
    pasar a vivo.

    FALLA CERRADA: bitácora de kubera apagada, `FANOUT_FUENTE`
    con errata, el libro ilegible (`stock_libro.LibroNoDisponible`: sin vista, sin
    T0, sin bodegas que cuenten, la base caída), el libro con muchos menos SKUs que
    los que vio la pasada anterior (`_PISO`), la foto ilegible o más cambios
    que el tope → la pasada aborta sin guardar ni encolar nada, y la segunda
    seguida avisa por Slack (`_sin_repartir`).
    """
    global _racha_sin_repartir
    from services import fanout_stock, stock_libro, stock_watch_read

    if not getattr(settings, "supabase_write_fanout_log", False):
        return await _sin_repartir(
            "bitacora_apagada", "SUPABASE_WRITE_FANOUT_LOG está apagada: sin `ops.fanout_log` los "
            "cambios del libro se repartirían sin rastro y el recuperador no tendría con qué "
            "comparar. La pasada no hizo nada.", "libro")
    try:
        fanout_libro = stock_libro.fuente_fanout() == "kubera"
    except stock_libro.LibroNoDisponible as exc:
        return await _sin_repartir("fanout_fuente_invalida", f"{exc}. La pasada no hizo nada.", "libro")
    try:
        libro = await asyncio.to_thread(stock_libro.todos)
    except stock_libro.LibroNoDisponible as exc:
        return await _sin_repartir("libro_no_disponible", f"{exc}. Pasada abortada: no se guardó "
                                                          f"ni se encoló nada.", "libro")
    try:
        foto = await asyncio.to_thread(stock_watch_read.foto_leer, True)
    except Exception as exc:  # noqa: BLE001 — ver `_foto`: «no sé» no es «no había nada»
        return await _sin_repartir("foto_no_disponible", f"No se pudo leer la foto anterior ({exc}). "
                                                         f"Pasada abortada; nada se encoló.", "libro")

    vistos = _vistos_con_piezas(foto)
    con_piezas = sum(1 for n in libro.values() if n > 0)
    if not forzar and con_piezas < len(vistos) * _PISO:
        msg = (f"El libro trae {con_piezas:,} SKUs con piezas y la pasada anterior vio {len(vistos):,}: "
               f"no se le cree. NO se encoló nada. Revisar y forzar si es real.")
        await asyncio.to_thread(_anotar, "*", "stock_watch_freno", "lectura del libro incompleta",
                                msg, False)
        return await _sin_repartir("libro_parcial", msg, "libro")

    cambios, fuera = _movidos_libro(libro, foto)
    a_cero = sum(1 for s, _a, _b in cambios if s.upper() in fuera)
    total = len(cambios)
    if total > tope() and not forzar:
        msg = (f"{total} cambios del libro en una pasada (tope {tope()})"
               + (f", {a_cero} de ellos a 0 porque el libro ya no los trae" if a_cero else "")
               + ". NO se encoló nada. Revisar y forzar si es real.")
        await asyncio.to_thread(_anotar, "*", "stock_watch_freno", "cortacircuitos", msg, False)
        _ultimo.update(cambios=total, odoo_deltas=0, woo_cambios=0, vendible_cambios=len(cambios),
                       vendible_sin_numero=a_cero)
        return await _sin_repartir("frenado", msg, "libro")

    registro = solo_registro() or not fanout_libro
    encolados = 0
    for sku, ant, ahora in cambios:
        sale = sku.upper() in fuera
        await asyncio.to_thread(
            _anotar, sku, "vendible_registro" if registro else "vendible_cambio",
            f"vendible {_txt(ant)} -> {ahora} (libro{': no lo trae' if sale else ''})",
            f"{_txt(ant)} -> {ahora} (replicar a canales)", False, registro)
        if not registro:
            fanout_stock.encolar(sku, motivo="cambio de vendible en kubera")
            encolados += 1

    # FOTO: lo leído. Sin repartir (registro), cada cambio conserva el `stock_kubera`
    # que tenía —NULL incluido: nunca se guarda un número de Woo en la columna del
    # libro—. Lo que el libro ya no trae queda en 0 (D4).
    kub = {str(s).upper(): v.get("kubera") for s, v in foto.items()}
    viejo = {s: kub.get(s.upper()) for s, _a, _b in cambios} if registro else {}
    await asyncio.to_thread(stock_watch_read.foto_guardar_libro,
                            [(s, viejo[s] if s in viejo else n) for s, n in libro.items()]
                            + [(s, viejo[s] if s in viejo else 0) for s in sorted(fuera)])

    _racha_sin_repartir = 0
    for k in ("cambios", "nota", "skus", "seguidas_sin_repartir"):
        _ultimo.pop(k, None)
    _ultimo.update(estado="ok", ts=time.time(), fuente="libro", segundos=round(time.time() - t0, 1),
                   skus_libro=len(libro), odoo_deltas=0, woo_escritos=0, woo_cambios=0,
                   # `vendible_sin_numero` (nombre del contrato con INVENTARIO FANOUT): los
                   # SKUs que salieron del libro y fueron a 0 en esta pasada.
                   vendible_cambios=len(cambios), vendible_sin_numero=a_cero,
                   encolados=encolados, fanout_lee_libro=fanout_libro, solo_registra=registro,
                   muestra=[f"{s}: libro {_txt(a)}→{b} → canales" for s, a, b in cambios[:12]])
    if total:
        log.info("stock_watch[libro]: %d cambios del vendible (%d encolados) · %d salieron del libro (a 0)%s",
                 len(cambios), encolados, a_cero,
                 "" if not registro else (" [SOLO REGISTRO]" if fanout_libro
                                          else " [SOLO REGISTRO: el fan-out todavía lee Woo]"))
    return dict(_ultimo)
