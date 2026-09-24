"""
odoo_ventas.py — La venta de un marketplace se vuelve ORDEN DE VENTA en Odoo.

POR QUÉ EXISTE
──────────────
Odoo ya recibe solas las ventas de Mercado Libre (5,214 órdenes `ML <id>` que
crea el módulo `meli_oerp`). Las de TikTok, Temu y Shein las captura A MANO
Gabriela: 216 + 49 + 420 órdenes, sin `client_order_ref` — es decir, sin
ninguna liga al marketplace y sin defensa contra duplicados. Este módulo hace
para TikTok/Temu lo que `meli_oerp` hace para ML.

LA DECISIÓN QUE MANDA: **ODOO DESCUENTA, WOO NO** (dale de Brandon, 27-ago-2026)
────────────────────────────────────────────────────────────────────────────────
Cada venta tiene que descontar UNA sola vez. Antes de este módulo descontaba
dos veces, y está medido en producción:

    26-ago 03:03–03:10  se capturan a mano 6 pzas de MUN-0023-MUL (TikTok)
    26-ago 03:18:42     stock_watch: "delta de Odoo (foto 103 -> 97)" → Woo 39 -> 33

…y esas ventas YA le habían bajado su pieza a Woo al volverse pedido. Del 21 al
26 de agosto, `stock_watch` le quitó a Woo **95 piezas de ese solo SKU** por
deltas de Odoo.

La cadena correcta, y la que este módulo asume, es una sola:

    venta → orden de venta en Odoo → (confirmar) reserva → free_qty baja
          → stock_watch → Woo → fan-out → los demás canales

Por eso el pedido de Woo de estos canales pasa a nacer PROTEGIDO
(`proteger_stock=True`), igual que ML FULL y Amazon FBA: el descuento real
llega por Odoo. El costo conocido de esta decisión es la LATENCIA — hasta 20
minutos entre la venta y el descuento en los canales, donde antes era
instantáneo.

EL ALMACÉN SE ELIGE POR STOCK, NO POR COSTUMBRE
───────────────────────────────────────────────
Gabriela suele usar TEXCO, pero la mercancía no siempre está ahí. Medido:

    VIA-0024-NEG   TEXCO: 0 libres   ·   TEXCO II: 30 físicas, 1 libre

Fijar TEXCO a secas haría nacer la orden en un almacén sin mercancía: Odoo la
confirma igual (NO bloquea por falta de stock — probado con 10,000 piezas), el
picking se queda en `confirmed` sin reservar nunca, y `free_qty` NO baja. O sea
que la venta no descontaría en ningún lado — que con la decisión de arriba
significa sobreventa silenciosa en todos los canales.

Así que el almacén se ELIGE: se lee `free_qty` con `context={"warehouse": id}`
y gana el primero que cubra la orden completa. Si ninguno la cubre, se toma el
que cubra más y **queda anotado como cobertura parcial** — nunca en silencio.

TODO ESTO ES SÍNCRONO Y BLOQUEA (regla 11)
──────────────────────────────────────────
XML-RPC es bloqueante. Cada función de aquí se llama desde `asyncio.to_thread`;
llamarlas dentro de una corrutina detiene el backend ENTERO mientras Odoo
contesta, no solo a quien llamó. Costó el apagón del 13-ago.
"""
from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from config import settings
from services import odoo

log = logging.getLogger("omnicanal.odoo_ventas")

# El partner por canal. Son los MISMOS que ya usa Gabriela, a propósito: el
# almacén no tiene que aprender nada nuevo, y así el comprador real NUNCA entra
# a Odoo (TikTok marca `seller.order.info` como dato personal — meterlo aquí
# sería regar PII sin necesidad: quien envía es el marketplace, no nosotros).
_PARTNER = {"tiktok": 1739238, "temu": 1738206}

# Cómo se escribe el canal donde lo va a LEER una persona (`origin` y la nota de
# la orden). `canal.capitalize()` producía "Tiktok", que en la pantalla del
# almacén se ve como un error de dedo.
_ETIQUETA = {"tiktok": "TikTok", "temu": "Temu"}

# Orden de preferencia. DROP OFF (142) queda FUERA a propósito: es el almacén de
# drop-shipping, no mercancía nuestra que se pueda surtir.
_ALMACENES = [(135, "TEXCO"), (150, "TEXCO II")]


def _kw(modelo: str, metodo: str, args: list, kwargs: dict | None = None) -> Any:
    """Llamada cruda a Odoo reutilizando la sesión de `services.odoo`."""
    uid = odoo._uid()
    if not uid:
        raise RuntimeError("Odoo: no se pudo autenticar")
    return odoo._models().execute_kw(
        settings.odoo_db, uid, settings.odoo_password, modelo, metodo,
        args, kwargs or {})


# ── El interruptor ──────────────────────────────────────────────────────────
# El switch del panel manda; la variable de entorno es el valor por omisión.
# Ver el encabezado de `ops.automatizacion_flags` en la migración 0033 para el
# porqué (un apagado en memoria se deshace solo en el siguiente deploy).
#
# CACHÉ CON VENCIMIENTO, y no es optimización prematura: `habilitado()` se
# consulta en CADA venta —incluidas las ~3,700 semanales de Mercado Libre que
# ni siquiera van a Odoo—, y es una lectura a kubera con psycopg2, que BLOQUEA.
# Sin caché, cada venta pagaría un viaje a Postgres para preguntar algo que
# cambia una vez al mes.
_FLAG = "odoo_ventas_enabled"
_cache: dict[str, Any] = {"valor": None, "ts": 0.0, "por": None, "motivo": None}
_TTL = 30.0          # segundos: un apagado tarda a lo más medio minuto en surtir


def _leer_flag() -> dict[str, Any] | None:
    """El interruptor persistido, o None si nadie lo ha tocado / no hay tabla."""
    from services import supabase_db as sdb
    try:
        return sdb.fetch_one(
            "select valor, motivo, actualizado_por, actualizado_at "
            "from ops.automatizacion_flags where flag = %(f)s", {"f": _FLAG})
    except Exception as exc:  # noqa: BLE001
        # Sin tabla (migración 0033 sin aplicar) o kubera caída: se cae al valor
        # por omisión. Se avisa una vez cada TTL, no en cada venta.
        log.debug("odoo_ventas: no se pudo leer el interruptor (%s)", exc)
        return None


def habilitado(refrescar: bool = False) -> bool:
    """
    ¿Está encendida la generación de órdenes en Odoo?

    ⚠️ BLOQUEA cuando el caché vence: llamar desde un hilo (`asyncio.to_thread`),
    nunca dentro de una corrutina (regla 11).
    """
    ahora = time.time()
    if refrescar or _cache["valor"] is None or (ahora - _cache["ts"]) > _TTL:
        fila = _leer_flag()
        _cache.update(
            valor=(bool(fila["valor"]) if fila
                   else bool(getattr(settings, "odoo_ventas_enabled", False))),
            ts=ahora,
            por=(fila or {}).get("actualizado_por"),
            motivo=(fila or {}).get("motivo"),
            persistido=bool(fila))
    return bool(_cache["valor"])


# ── El interruptor POR CANAL ────────────────────────────────────────────────
# Cada canal tiene su propia fila en `ops.automatizacion_flags` con la llave
# `odoo_ventas_canal_<canal>`. No hizo falta migración: la tabla ya es
# (flag, valor), justo la forma que esto necesita.
#
# POR QUÉ POR CANAL Y NO UNA LISTA. TikTok y Temu no están en el mismo punto:
# TikTok lleva semanas de observación y Temu acaba de estrenar su webhook. Un
# solo interruptor obligaría a encenderlos juntos, y apagar Temu por un
# problema suyo se llevaría a TikTok por delante.
_cache_canales: dict[str, dict[str, Any]] = {}


def _flag_canal(canal: str) -> str:
    return f"{_FLAG}_canal_{canal}"


def canal_activo(canal: str, refrescar: bool = False) -> bool:
    """¿Está encendido ESE canal? ⚠️ BLOQUEA: llamar desde un hilo."""
    canal = (canal or "").lower()
    if canal not in _CANALES_POSIBLES:
        return False
    c = _cache_canales.setdefault(canal, {"valor": None, "ts": 0.0})
    ahora = time.time()
    if refrescar or c["valor"] is None or (ahora - c["ts"]) > _TTL:
        fila = None
        from services import supabase_db as sdb
        try:
            fila = sdb.fetch_one(
                "select valor, motivo, actualizado_por from ops.automatizacion_flags "
                "where flag = %(f)s", {"f": _flag_canal(canal)})
        except Exception as exc:  # noqa: BLE001
            log.debug("odoo_ventas: interruptor de %s no legible (%s)", canal, exc)
        if fila:
            c.update(valor=bool(fila["valor"]), persistido=True,
                     por=fila.get("actualizado_por"), motivo=fila.get("motivo"))
        else:
            # Sin fila: manda la variable de entorno, que es el valor por omisión.
            crudo = str(getattr(settings, "odoo_ventas_canales", "") or "")
            porom = {x.strip().lower() for x in crudo.split(",") if x.strip()}
            c.update(valor=canal in porom, persistido=False, por=None, motivo=None)
        c["ts"] = ahora
    return bool(c["valor"])


def fijar_canal(canal: str, encendido: bool, quien: str = "",
                motivo: str = "") -> dict[str, Any]:
    """Mueve el switch de UN canal. Nunca lanza."""
    from services import supabase_db as sdb
    canal = (canal or "").lower()
    if canal not in _CANALES_POSIBLES:
        return {"ok": False, "motivo": f"canal '{canal}' no soportado"}
    try:
        sdb.execute(
            """insert into ops.automatizacion_flags
                   (flag, valor, motivo, actualizado_at, actualizado_por)
               values (%(f)s, %(v)s, %(m)s, now(), %(q)s)
               on conflict (flag) do update set
                   valor = excluded.valor, motivo = excluded.motivo,
                   actualizado_at = now(), actualizado_por = excluded.actualizado_por""",
            {"f": _flag_canal(canal), "v": bool(encendido),
             "m": (motivo or "")[:300] or None, "q": (quien or "")[:120] or None})
        log.warning("Órdenes de venta en Odoo · canal %s: %s por %s%s", canal,
                    "ENCENDIDO" if encendido else "APAGADO", quien or "?",
                    f" — {motivo}" if motivo else "")
        return {"ok": True, **estado_interruptor()}
    except Exception as exc:  # noqa: BLE001
        log.exception("no se pudo mover el interruptor del canal %s", canal)
        return {**estado_interruptor(), "ok": False, "motivo": str(exc)[:300]}


# ── El interruptor de ESPERAR LA GUÍA (23-sep-2026) ─────────────────────────
# "Sólo crear el ESPACIO de la orden en omnicanal… pero NO generar la orden en
# Odoo hasta tener la guía de Temu" (Brandon). Con esto encendido para un canal,
# `crear_orden` calcula todo y NO escribe; la orden nace después, en el trabajo
# de guías, cuando el canal por fin entrega la guía.
#
# Vive en la misma tabla y con la misma forma que el switch por canal, por lo
# mismo: apagarlo no puede exigir un deploy. Llave
# `odoo_ventas_espera_guia_canal_<canal>`.
_FLAG_ESPERA = "odoo_ventas_espera_guia"
_cache_espera: dict[str, dict[str, Any]] = {}

# QUIÉN CREA LA ORDEN DESPUÉS. Es el trabajo de guías de cada canal, y por eso
# esta bandera FALLA CERRADO si ese trabajo está apagado: diferir la creación
# sin nadie que la retome deja la venta en el limbo para siempre. Mejor una
# orden de más en el tablero del almacén que una venta que nunca llega.
_JOB_GUIAS = {"tiktok": "tiktok_guias_enabled", "temu": "temu_guias_enabled"}


def _flag_espera(canal: str) -> str:
    return f"{_FLAG_ESPERA}_canal_{canal}"


def espera_guia_activa(canal: str, refrescar: bool = False) -> bool:
    """
    ¿Este canal ESPERA la guía para crear la orden? ⚠️ BLOQUEA: desde un hilo.

    Dice sólo eso. NO dice si se va a crear: eso lo deciden los interruptores de
    arriba, que mandan (ver `crear_orden`). Con `SOLO_REGISTRO=true` esto no
    cambia nada, porque no había nada que diferir.
    """
    canal = (canal or "").lower()
    if canal not in _CANALES_POSIBLES:
        return False
    if not bool(getattr(settings, _JOB_GUIAS.get(canal, ""), False)):
        # El trabajo que crearía la orden después no corre: se crea al vender,
        # como siempre. Se avisa fuerte porque es una bandera encendida que no
        # está haciendo lo que su nombre dice.
        if _pidio_espera(canal):
            log.warning("odoo_ventas: %s pide esperar la guía pero su trabajo de "
                        "guías (%s) está APAGADO: nadie crearía la orden después. "
                        "Se sigue creando al vender.", canal, _JOB_GUIAS.get(canal))
            _avisar_huerfanas(canal)
        return False
    c = _cache_espera.setdefault(canal, {"valor": None, "ts": 0.0})
    ahora = time.time()
    if refrescar or c["valor"] is None or (ahora - c["ts"]) > _TTL:
        c.update(**_pedir_flag_espera(canal), ts=ahora)
    return bool(c["valor"])


# Cada cuánto se puede repetir el aviso de huérfanas por canal. `espera_guia_
# activa` se llama una vez por venta; sin esta pausa, un día con tráfico llenaría
# la campana con la misma noticia.
_AVISO_HUERFANAS_S = 3600.0
_aviso_huerfanas: dict[str, float] = {}


def _avisar_huerfanas(canal: str) -> int:
    """
    Cuenta las esperas que se quedaron SIN NADIE QUE LAS CREE y lo sube a la
    campana. ⚠️ BLOQUEA (ya corre dentro del hilo de `crear_orden`). Nunca lanza.

    EL CASO. La guarda de arriba falla cerrado sólo HACIA ADELANTE: al apagar el
    trabajo de guías, las ventas nuevas vuelven a crearse al vender. Pero las
    filas `espera_guia` que YA existían no las retoma nadie —el scheduler ni
    siquiera registra el job—: siguen escondiendo stock hasta caducar y después
    desaparecen sin orden. Y el aviso que había ("espera pedida, pero inactiva")
    suena a configuración incoherente, no a nueve ventas en el limbo.

    Así que se dice el NÚMERO, y se dice por la campana, que es donde se mira
    cuando nadie está abriendo el panel. La salida a mano es
    `POST /api/automatizacion/espera/drenar`.
    """
    ahora = time.time()
    if ahora - _aviso_huerfanas.get(canal, 0.0) < _AVISO_HUERFANAS_S:
        return 0
    _aviso_huerfanas[canal] = ahora
    try:
        from services import odoo_ventas_log
        n = int((odoo_ventas_log.contar_esperas(
            canal, dias=_dias_espera()) or {}).get("ventas") or 0)
    except Exception as exc:  # noqa: BLE001
        log.debug("_avisar_huerfanas(%s): %s", canal, exc)
        return 0
    if not n:
        return 0
    msg = (f"{n} venta(s) de {canal} esperan su guía y su trabajo de guías "
           f"({_JOB_GUIAS.get(canal)}) está APAGADO: nadie va a crear esas "
           f"órdenes en Odoo, y mientras tanto siguen escondiendo su stock. "
           f"Se drenan con POST /api/automatizacion/espera/drenar?canal={canal}.")
    log.error("odoo_ventas: %s", msg)
    try:
        from services import alertas
        # `avisar_estado` y no `avisar`: esto DURA días (el job sigue apagado
        # hasta que alguien lo encienda), y con `avisar` serían cuatro mensajes
        # idénticos al día. Además así se anuncia la recuperación —el drenaje o
        # el reencendido— en vez de que el aviso se apague en silencio.
        alertas.avisar_estado(
            f"odoo_espera_huerfana:{canal}", "huerfanas", msg,
            texto_ok=(f"Ya no quedan ventas de {canal} esperando guía sin quién "
                      f"las cree."))
    except Exception as exc:  # noqa: BLE001 — la campana nunca rompe nada
        log.debug("_avisar_huerfanas: no se pudo subir a la campana (%s)", exc)
    return n


def _dias_espera() -> int:
    """
    LA VENTANA DE LA ESPERA, en un solo sitio. ⚠️ Leerla de aquí y de ningún
    otro lado es el arreglo, no un detalle.

    Había TRES ajustes que decían ser el mismo: `odoo_ventas_espera_guia_dias`
    (que usa `stock_watch._ventana_pendientes` para decidir cuánto stock
    esconder) y `temu_guias_dias`/`tiktok_guias_dias` (que el scheduler le pasa
    al trabajo de guías y llegaban hasta `pendientes_sin_orden`). Coincidían en
    14 por omisión y los comentarios afirmaban que no podían desincronizarse.

    Podían. Bajar `TEMU_GUIAS_DIAS` a 7 —una variable de rendimiento, nadie
    pensaría que toca inventario— dejaba siete días de mercancía escondida sin
    nadie que fuera a crear la orden; subirlo a 21 hacía lo contrario y peor:
    las piezas volvían al anaquel el día 14 mientras el trabajo todavía crearía
    la orden el 20, y se sobrevende. Ahora la ventana de la espera es ÉSTA, el
    `dias` del job manda sólo sobre la cola de Odoo, y no hay dos números que
    puedan separarse.
    """
    return max(1, int(getattr(settings, "odoo_ventas_espera_guia_dias", 14) or 14))


def _huerfanas(canal: str) -> dict[str, Any]:
    """Las esperas vivas de un canal, para el panel. ⚠️ BLOQUEA. Nunca lanza."""
    try:
        from services import odoo_ventas_log
        return odoo_ventas_log.contar_esperas(canal, dias=_dias_espera())
    except Exception as exc:  # noqa: BLE001
        log.debug("_huerfanas(%s): %s", canal, exc)
        return {"ventas": 0, "mas_vieja_h": 0.0}


def _limite_espera(limite: int) -> int:
    """El tope de la mitad de espera de la cola. Usa
    `ODOO_VENTAS_ESPERA_GUIA_LIMITE` —que hasta ahora no leía NADIE: una
    variable de configuración que nadie lee es una promesa de control que no
    existe— y nunca pide más de lo que cabe en la vuelta."""
    return max(1, min(int(limite),
                      int(getattr(settings, "odoo_ventas_espera_guia_limite", 60) or 60)))


def _pedir_flag_espera(canal: str) -> dict[str, Any]:
    """El valor persistido del switch, o el de la variable de entorno."""
    from services import supabase_db as sdb
    fila = None
    try:
        fila = sdb.fetch_one(
            "select valor, motivo, actualizado_por from ops.automatizacion_flags "
            "where flag = %(f)s", {"f": _flag_espera(canal)})
    except Exception as exc:  # noqa: BLE001
        log.debug("odoo_ventas: espera de guía de %s no legible (%s)", canal, exc)
    if fila:
        return {"valor": bool(fila["valor"]), "persistido": True,
                "por": fila.get("actualizado_por"), "motivo": fila.get("motivo")}
    return {"valor": _por_omision_espera(canal), "persistido": False,
            "por": None, "motivo": None}


def _por_omision_espera(canal: str) -> bool:
    crudo = str(getattr(settings, "odoo_ventas_espera_guia_canales", "") or "")
    return canal in {x.strip().lower() for x in crudo.split(",") if x.strip()}


def _pidio_espera(canal: str) -> bool:
    """¿Alguien pidió esperar la guía en este canal? SIN mirar si se puede.
    Sólo para avisar del caso incoherente; no decide nada."""
    c = _cache_espera.get(canal)
    if c and c.get("valor") is not None:
        return bool(c["valor"])
    return _por_omision_espera(canal)


def fijar_espera_guia(canal: str, encendido: bool, quien: str = "",
                      motivo: str = "") -> dict[str, Any]:
    """Mueve el switch de "esperar la guía" de UN canal. Nunca lanza."""
    from services import supabase_db as sdb
    canal = (canal or "").lower()
    if canal not in _CANALES_POSIBLES:
        return {"ok": False, "motivo": f"canal '{canal}' no soportado"}
    try:
        sdb.execute(
            """insert into ops.automatizacion_flags
                   (flag, valor, motivo, actualizado_at, actualizado_por)
               values (%(f)s, %(v)s, %(m)s, now(), %(q)s)
               on conflict (flag) do update set
                   valor = excluded.valor, motivo = excluded.motivo,
                   actualizado_at = now(), actualizado_por = excluded.actualizado_por""",
            {"f": _flag_espera(canal), "v": bool(encendido),
             "m": (motivo or "")[:300] or None, "q": (quien or "")[:120] or None})
        log.warning("Esperar la guía para crear en Odoo · canal %s: %s por %s%s", canal,
                    "ENCENDIDO" if encendido else "APAGADO", quien or "?",
                    f" — {motivo}" if motivo else "")
        return {"ok": True, **estado_interruptor()}
    except Exception as exc:  # noqa: BLE001
        log.exception("no se pudo mover la espera de guía del canal %s", canal)
        return {**estado_interruptor(), "ok": False, "motivo": str(exc)[:300]}


def estado_interruptor() -> dict[str, Any]:
    """Lo que pinta el switch: el general, y el de cada canal."""
    habilitado(refrescar=True)
    canales_estado = {}
    for c in sorted(_CANALES_POSIBLES):
        canal_activo(c, refrescar=True)
        d = _cache_canales.get(c, {})
        espera = _pedir_flag_espera(c)
        _cache_espera.setdefault(c, {}).update(**espera, ts=time.time())
        # UNA sola vez: además de leer la guarda del job, puede disparar el
        # aviso de huérfanas (y ése hace una consulta y habla con la campana).
        activa = espera_guia_activa(c)
        canales_estado[c] = {
            "encendido": bool(d.get("valor")),
            "persistido": bool(d.get("persistido")),
            "actualizado_por": d.get("por"),
            "motivo": d.get("motivo"),
            # Lo que alguien PIDIÓ y lo que de verdad está pasando: si el
            # trabajo de guías está apagado, el switch dice "sí" y el flujo
            # crea al vender. La pantalla tiene que poder decir las dos cosas.
            #
            # `refrescar=False` a propósito: el caché se acaba de llenar dos
            # líneas arriba con `_pedir_flag_espera`, y pedirlo otra vez sería
            # una segunda lectura a kubera POR CANAL en un endpoint que ya
            # bloquea. Lo único que añade esta llamada es la guarda del job.
            "espera_guia": bool(espera["valor"]),
            "espera_guia_activa": activa,
            "espera_guia_persistida": bool(espera["persistido"]),
            "espera_guia_por": espera.get("por"),
            # CUÁNTAS quedaron colgadas. Sólo se cuenta cuando la espera está
            # PEDIDA pero el trabajo de guías apagado, que es el único caso en
            # que el número cambia lo que hay que hacer: el chip ámbar pasa de
            # "espera pedida, pero inactiva" —que suena inocuo— a "9 ventas sin
            # quién las cree". Una consulta más sólo en ese caso.
            "espera_huerfanas": (
                int((_huerfanas(c) or {}).get("ventas") or 0)
                if bool(espera["valor"]) and not activa else 0),
        }
    return {
        "encendido": bool(_cache["valor"]),
        "persistido": bool(_cache.get("persistido")),
        "por_omision": bool(getattr(settings, "odoo_ventas_enabled", False)),
        "actualizado_por": _cache.get("por"),
        "motivo": _cache.get("motivo"),
        "canales_estado": canales_estado,
    }


def fijar_interruptor(encendido: bool, quien: str = "",
                      motivo: str = "") -> dict[str, Any]:
    """Mueve el switch. Devuelve el estado resultante; nunca lanza."""
    from services import supabase_db as sdb
    try:
        sdb.execute(
            """insert into ops.automatizacion_flags
                   (flag, valor, motivo, actualizado_at, actualizado_por)
               values (%(f)s, %(v)s, %(m)s, now(), %(q)s)
               on conflict (flag) do update set
                   valor = excluded.valor, motivo = excluded.motivo,
                   actualizado_at = now(), actualizado_por = excluded.actualizado_por""",
            {"f": _FLAG, "v": bool(encendido), "m": (motivo or "")[:300] or None,
             "q": (quien or "")[:120] or None})
        log.warning("Órdenes de venta en Odoo: %s por %s%s",
                    "ENCENDIDAS" if encendido else "APAGADAS", quien or "?",
                    f" — {motivo}" if motivo else "")
        return {"ok": True, **estado_interruptor()}
    except Exception as exc:  # noqa: BLE001
        log.exception("no se pudo mover el interruptor de odoo_ventas")
        # El `**estado_interruptor()` va PRIMERO: ese dict trae su propia llave
        # `motivo` (el del apagado guardado) y, puesto después, se comía el
        # mensaje del error. El panel mostraba el fallo en blanco — que es la
        # peor forma de fallar en un botón de encendido.
        return {**estado_interruptor(), "ok": False, "motivo": str(exc)[:300]}


# Los canales que este módulo SABE atender. Es una constante y no una consulta
# a propósito: el seam la usa como filtro barato en CADA venta —incluidas las
# ~3,700 semanales de Mercado Libre, que nunca van a Odoo por aquí— y una
# lectura a kubera ahí dentro bloquearía la corrutina de la venta (regla 11).
# La decisión REAL por canal se toma dentro del hilo, en `crear_orden`.
_CANALES_POSIBLES = frozenset({"tiktok", "temu"})


def canales_posibles() -> frozenset[str]:
    return _CANALES_POSIBLES


def canales() -> set[str]:
    """Los canales encendidos HOY. El switch por canal manda; la variable de
    entorno es el valor por omisión.

    ⚠️ BLOQUEA: llamar desde un hilo, nunca dentro de una corrutina."""
    return {c for c in _CANALES_POSIBLES if canal_activo(c)}


# ── Resolución de producto ──────────────────────────────────────────────────

def productos_por_sku(skus: list[str]) -> dict[str, dict[str, Any]]:
    """{ sku: {id, name, list_price} } por `default_code`. Los que no existan
    simplemente no aparecen — el que llama DEBE notar la ausencia."""
    limpios = sorted({(s or "").strip() for s in skus if (s or "").strip()})
    if not limpios:
        return {}
    filas = _kw("product.product", "search_read",
                [[["default_code", "in", limpios]]],
                {"fields": ["default_code", "name", "list_price", "uom_id"]})
    return {(f["default_code"] or "").strip(): f for f in filas}


def libre_por_almacen(product_ids: list[int]) -> dict[int, dict[int, float]]:
    """
    { product_id: { warehouse_id: libres } }.

    `free_qty` con `context={"warehouse": id}` es lo que de verdad se puede
    prometer EN ESE ALMACÉN: ya le restó lo comprometido por otras órdenes.
    Se pide almacén por almacén porque el contexto es uno por lectura.
    """
    salida: dict[int, dict[int, float]] = {p: {} for p in product_ids}
    if not product_ids:
        return salida
    for wid, _nombre in _ALMACENES:
        filas = _kw("product.product", "read", [product_ids, ["free_qty"]],
                    {"context": {"warehouse": wid}})
        for f in filas:
            salida.setdefault(f["id"], {})[wid] = float(f.get("free_qty") or 0)
    return salida


def planear_almacenes(lineas: list[dict[str, Any]],
                      libres: dict[int, dict[int, float]]) -> dict[str, Any]:
    """
    De qué almacén sale cada pieza. Puede devolver MÁS DE UNA parte.

    LAS TRES REGLAS (Brandon, 2026-09-01), en este orden:

      1. **Si un almacén solo cubre la orden completa, se usa ése**, aunque el
         otro también tenga. Gana TEXCO por preferencia.
      2. **Si solo uno alcanza, van TODAS las piezas ahí** — no se parte por
         gusto. Pide 3, TEXCO tiene 2 y TEXCO II tiene 10 → las 3 a TEXCO II.
      3. **Si ninguno alcanza solo, se PARTE**: pide 3, TEXCO tiene 2 y TEXCO II
         tiene 1 → dos órdenes, una de 2 en TEXCO y otra de 1 en TEXCO II.

    La 2 es la que evita el error tentador: repartir en cuanto el primero no
    alcanza, y acabar con dos entregas donde bastaba una.

    Devuelve `partes` (una por almacén con piezas asignadas), la `cobertura`
    —`completa`, `dividida` o `parcial`— y la FOTO del stock, que es del momento
    y no se puede reconstruir después.
    """
    foto: dict[str, dict[str, float]] = {}
    for ln in lineas:
        por_alm = libres.get(ln["product_id"], {})
        foto[ln["sku"]] = {str(wid): por_alm.get(wid, 0.0) for wid, _n in _ALMACENES}

    def _libre(ln, wid) -> int:
        return int(libres.get(ln["product_id"], {}).get(wid, 0) or 0)

    # ── Reglas 1 y 2: ¿algún almacén, SOLO, cubre todo? ────────────────────
    for wid, nombre in _ALMACENES:
        if all(_libre(ln, wid) >= int(ln["cantidad"]) for ln in lineas):
            return {"partes": [{"almacen_id": wid, "almacen": nombre,
                                "lineas": [dict(l) for l in lineas]}],
                    "cobertura": "completa", "stock_foto": foto, "faltante": {}}

    # ── Regla 3: ninguno alcanza solo → se reparte, por preferencia ────────
    restante = {id(ln): int(ln["cantidad"]) for ln in lineas}
    partes: list[dict[str, Any]] = []
    for wid, nombre in _ALMACENES:
        asignadas = []
        for ln in lineas:
            falta = restante[id(ln)]
            if falta <= 0:
                continue
            toma = min(_libre(ln, wid), falta)
            if toma > 0:
                asignadas.append({**ln, "cantidad": toma})
                restante[id(ln)] -= toma
        if asignadas:
            partes.append({"almacen_id": wid, "almacen": nombre, "lineas": asignadas})

    faltante = {ln["sku"]: restante[id(ln)] for ln in lineas if restante[id(ln)] > 0}
    if faltante:
        # No hay en NINGÚN almacén. Las piezas huérfanas se cuelgan de la
        # primera parte —o de TEXCO si no hubo ninguna— y la orden queda marcada
        # `parcial`: que el almacén VEA la venta y sepa que le falta mercancía
        # es mejor que no enterarse. Es sobreventa, y la pantalla la pinta ámbar.
        if not partes:
            partes = [{"almacen_id": _ALMACENES[0][0], "almacen": _ALMACENES[0][1],
                       "lineas": []}]
        destino = partes[0]
        for ln in lineas:
            sobra = restante[id(ln)]
            if sobra <= 0:
                continue
            ya = next((x for x in destino["lineas"] if x["sku"] == ln["sku"]), None)
            if ya:
                ya["cantidad"] += sobra
            else:
                destino["lineas"].append({**ln, "cantidad": sobra})

    return {"partes": partes, "stock_foto": foto, "faltante": faltante,
            "cobertura": ("parcial" if faltante
                          else "dividida" if len(partes) > 1 else "completa")}


def elegir_almacen(lineas: list[dict[str, Any]],
                   libres: dict[int, dict[int, float]]) -> dict[str, Any]:
    """
    Qué almacén surte esta orden. Gana el PRIMERO que la cubra completa.

    `lineas` = [{product_id, cantidad, sku}]. Devuelve el almacén, cómo quedó la
    cobertura y la FOTO del stock por SKU — esa foto es lo que se guarda como
    "stock al momento de la venta" y es irrepetible: dentro de 20 minutos ya no
    se puede reconstruir.
    """
    # LA FOTO SE LLAVEA POR ID DE ALMACÉN, NO POR NOMBRE. El nombre es una
    # etiqueta que alguien puede editar en Odoo cualquier martes; el id no. Con
    # llaves por nombre, renombrar "TEXCO II" hacía que la foto se guardara en
    # NULL **en silencio** — y esa foto es justo el dato irrecuperable.
    foto: dict[str, dict[str, float]] = {}
    for ln in lineas:
        por_alm = libres.get(ln["product_id"], {})
        foto[ln["sku"]] = {str(wid): por_alm.get(wid, 0.0) for wid, _n in _ALMACENES}

    mejor: dict[str, Any] | None = None
    for wid, nombre in _ALMACENES:
        cubiertas = sum(1 for ln in lineas
                        if libres.get(ln["product_id"], {}).get(wid, 0) >= ln["cantidad"])
        piezas = sum(min(libres.get(ln["product_id"], {}).get(wid, 0), ln["cantidad"])
                     for ln in lineas)
        completo = cubiertas == len(lineas)
        cand = {"almacen_id": wid, "almacen": nombre, "completo": completo,
                "lineas_cubiertas": cubiertas, "piezas_cubiertas": piezas}
        if completo:
            return {**cand, "cobertura": "completa", "stock_foto": foto}
        if mejor is None or piezas > mejor["piezas_cubiertas"]:
            mejor = cand

    # Ninguno cubre todo. Se toma el que más cubra, pero SE DICE: una orden que
    # nace sin respaldo no se reserva, y con "Odoo descuenta" eso es sobreventa
    # esperando a ocurrir. Que salga en la pantalla es el punto.
    return {**(mejor or {"almacen_id": _ALMACENES[0][0], "almacen": _ALMACENES[0][1],
                         "lineas_cubiertas": 0, "piezas_cubiertas": 0}),
            "cobertura": "parcial", "stock_foto": foto}


# ── Idempotencia ────────────────────────────────────────────────────────────

# Estados de sale.order en que la orden ya está confirmada (en Odoo 17, "done"
# es la confirmada y bloqueada). El PDF de la etiqueta sólo se sube a éstas.
_CONFIRMADAS = ("sale", "done")


def url_orden_publica() -> str:
    """Plantilla de la liga a una orden de venta, con `{id}` por sustituir.

    Sale de config (`ODOO_URL_PUBLICA`, `ODOO_WEB_*`) y la expone `/estado`,
    para que el panel no arme la liga por su cuenta con el host de la API.
    """
    base = (settings.odoo_url_publica or settings.odoo_url or "").rstrip("/")
    return (f"{base}/web#id={{id}}&cids={settings.odoo_web_cids}"
            f"&menu_id={settings.odoo_web_menu_venta}"
            f"&action={settings.odoo_web_action_venta}&model=sale.order&view_type=form")


def pendientes_de_guia(canal: str, dias: int = 14,
                       limite: int = 60) -> list[dict[str, Any]]:
    """
    Ventas del canal a las que todavía les falta ALGO de la guía.

    ⚠️ BLOQUEA: llamar desde un hilo.

    Una venta sigue pendiente mientras le falte cualquiera de las DOS cosas que
    pidió Brandon (11-sep):
      · el número de rastreo en su ENTREGA de salida (`carrier_tracking_ref`), y
      · el PDF de la etiqueta en la ORDEN, en el campo "Subir guía"
        (`meli_etiqueta_file`) — la convención de la casa, la misma que ya
        traen las órdenes de SHEIN (`JMX….pdf`).

    LA COLA SE LE PREGUNTA A ODOO, NO A LA BITÁCORA, y ésa es la corrección de
    diseño que hizo falta. La primera versión elegía por `guia = ''` en
    `ops.odoo_sale_orders`, pero esa columna la rellena el seam de la venta en
    cualquier re-aviso SIN tocar Odoo: la fila salía de la cola y la entrega se
    quedaba sin guía para siempre. Preguntando por lo que le falta a Odoo la
    cola se vacía sola cuando el trabajo está hecho, y un fallo se reintenta a
    la vuelta siguiente en vez de perderse.

    El PDF se revisa con `bin_size`: Odoo contesta el TAMAÑO en vez del binario,
    y no hay que bajar cientos de KB por orden sólo para saber si existe.

    SÓLO ENTREGAS DE SALIDA. Una orden en ruta de dos pasos cuelga PICK y PACK
    (internos) y, si hubo devolución, también su entrada. Estampar el rastreo
    del paquete en una transferencia interna o en una devolución es escribir un
    dato falso donde alguien lo va a leer.
    """
    canal = (canal or "").lower()
    partner = _PARTNER.get(canal)
    if not partner:
        return []
    desde = (datetime.now(timezone.utc) - timedelta(days=int(dias))
             ).strftime("%Y-%m-%d %H:%M:%S")
    ordenes = _kw("sale.order", "search_read",
                  [[["partner_id", "=", partner],
                    ["client_order_ref", "!=", False],
                    ["state", "!=", "cancel"],
                    ["create_date", ">=", desde]]],
                  {"fields": ["name", "client_order_ref", "picking_ids", "state",
                              "meli_etiqueta_file"],
                   "order": "create_date asc", "limit": 400,
                   "context": {"bin_size": True}})
    if not ordenes:
        return []
    return _cola_desde_ordenes(ordenes, limite)


def _cola_desde_ordenes(ordenes: list[dict[str, Any]], limite: int,
                        siempre_partes: bool = False) -> list[dict[str, Any]]:
    """
    De órdenes de Odoo ya leídas a renglones de cola. ⚠️ BLOQUEA (lee entregas).

    Es el cuerpo que comparten `pendientes_de_guia` —la cola del canal entero—
    y `cola_de_una_venta` —el renglón de UNA venta recién creada—. Se extrajo al
    diferir la creación hasta la guía: la orden nace y hay que escribirle el
    número y el PDF EN LA MISMA VUELTA (`fijar_etiqueta` sólo sube a órdenes
    confirmadas), así que hace falta armar su renglón sin esperar a la siguiente
    barrida por fecha.

    `siempre_partes` cuelga `partes` también a las ventas de UNA sola orden.
    `dividida` sigue significando lo de siempre —dos o más órdenes vivas— y es
    lo que decide el camino del emparejador; `partes` es sólo la forma de datos.
    """
    todos = [i for o in ordenes for i in (o.get("picking_ids") or [])]
    ids_faltan: set[int] = set()
    if todos:
        # Las de SALIDA que siguen sin rastreo. `state != cancel`: una entrega
        # cancelada ya no va a ninguna parte.
        faltan = _kw("stock.picking", "search_read",
                     [[["id", "in", todos],
                       ["picking_type_code", "=", "outgoing"],
                       ["state", "!=", "cancel"],
                       ["carrier_tracking_ref", "in", [False, ""]]]],
                     {"fields": ["id"]})
        ids_faltan = {p["id"] for p in faltan}
    # La VENTA es la misma para las dos mitades de un surtido dividido: el ref
    # lleva sufijo `#1`/`#2` y aquí se vuelve a unir en UN renglón de la cola.
    # Pero la guía NO tiene por qué ser una sola (ver `_agregar_partes`).
    por_venta: dict[str, dict[str, Any]] = {}
    # Todas las órdenes vivas de cada venta partida, tengan trabajo o no: para
    # emparejar paquetes hace falta saber qué lleva CADA parte, también la que
    # ya quedó completa (un SKU repartido entre las dos no sirve para decidir).
    todas_ordenes: dict[str, list[dict[str, Any]]] = {}
    for o in ordenes:
        venta = str(o["client_order_ref"]).split("#", 1)[0]
        if siempre_partes or "#" in str(o["client_order_ref"]):
            todas_ordenes.setdefault(venta, []).append(o)
        pend = [i for i in (o.get("picking_ids") or []) if i in ids_faltan]
        # El PDF sólo va a órdenes CONFIRMADAS (el flujo de Brandon: confirmar,
        # luego la etiqueta). Una en borrador espera: entra a la cola en la
        # vuelta siguiente a su confirmación. El número no necesita esta
        # guarda: un borrador no tiene entregas a las que escribirle.
        sin_pdf = (not o.get("meli_etiqueta_file")
                   and o.get("state") in _CONFIRMADAS)
        if not pend and not sin_pdf:
            continue
        d = por_venta.setdefault(venta, {"order_id": venta, "ordenes": [],
                                         "pickings": [], "sin_pdf": []})
        d["ordenes"].append(o["name"])
        d["pickings"].extend(pend)
        if sin_pdf:
            d["sin_pdf"].append(o["id"])
    # PARTIDA DE VERDAD = DOS O MÁS ÓRDENES VIVAS. Un ref con '#' y una sola
    # orden viva (la otra se canceló, o la #2 nunca se llegó a crear) es una
    # venta de una orden: va por el camino de siempre, el probado en producción,
    # y no por el emparejador.
    divididas = {v for v, os in todas_ordenes.items() if len(os) >= 2}
    salida = list(por_venta.values())[:int(limite)]
    con_partes = [d for d in salida
                  if d["order_id"] in (todas_ordenes if siempre_partes else divididas)]
    if con_partes:
        _agregar_partes(con_partes, todas_ordenes, ids_faltan, divididas)
    return salida


def cola_de_una_venta(canal: str, order_id: str) -> dict[str, Any] | None:
    """
    El renglón de cola de UNA venta, leído de Odoo ahora mismo. ⚠️ BLOQUEA.

    Para la creación diferida: la orden acaba de nacer y hay que escribirle el
    número y subirle el PDF SIN esperar a la vuelta siguiente. Devuelve la misma
    forma que `pendientes_de_guia` (con `partes` siempre, aunque sea una sola),
    o `None` si a esa venta ya no le falta nada.

    Se le pregunta a ODOO, nunca a la bitácora: el renglón tiene que describir lo
    que de verdad quedó escrito, incluido lo que pusiera otra persona a mano.
    """
    canal = (canal or "").lower()
    partner = _PARTNER.get(canal)
    if not partner or not str(order_id or "").strip():
        return None
    venta = str(order_id).strip()
    ordenes = _kw("sale.order", "search_read",
                  [[["partner_id", "=", partner], ["state", "!=", "cancel"],
                    "|", ["client_order_ref", "=", venta],
                    ["client_order_ref", "=like", f"{venta}#%"]]],
                  {"fields": ["name", "client_order_ref", "picking_ids", "state",
                              "meli_etiqueta_file"],
                   "order": "id asc", "context": {"bin_size": True}}) or []
    # `=like` trata `_` como comodín: se re-filtra por la forma exacta, igual
    # que en `partes_de_ventas`.
    propias = []
    for o in ordenes:
        ref = str(o.get("client_order_ref") or "")
        base, sep, suf = ref.partition("#")
        if base == venta and (not sep or suf.isdigit()):
            propias.append(o)
    if not propias:
        return None
    filas = _cola_desde_ordenes(propias, 1, siempre_partes=True)
    return filas[0] if filas else None


# ── Surtido dividido: una venta, varias órdenes, quizá varios paquetes ──────
#
# LO QUE ESTABA MAL (18-sep-2026, venta Temu PO-128-10289257052790014 → S38861
# en TEXCO + S38862 en TEXCO II). La cola unía las partes en un renglón y el
# refresco le escribía a TODAS las entregas la guía del PRIMER paquete que
# mencionara la venta. Si la venta sale en dos cajas —una por almacén, que es
# justo lo que pasa cuando se parte—, la segunda entrega se quedaba con la guía
# de la otra caja. Y `fijar_guia` no pisa lo que ya tiene: el error era para
# siempre.
#
# Ahora el renglón de una venta partida lleva `dividida=True` y `partes`, una
# por orden de Odoo, con SUS entregas pendientes, si le falta el PDF y QUÉ SKUs
# lleva. Con eso cada refresco decide parte por parte (`emparejar_partes`). Lo
# que devuelve la cola para una venta NO partida no cambia en nada.

def _num_parte(ref: str) -> int:
    """"PO-1#2" → 2; sin sufijo → 0 (la venta a secas va primero)."""
    _v, _s, suf = str(ref or "").partition("#")
    return int(suf) if suf.isdigit() else 0


def _lineas_de_ordenes(order_ids: list[int], kw: Any = None) -> dict[int, list[dict[str, Any]]]:
    """
    {sale_id: [{sku, titulo, cantidad}]} leído de Odoo. ⚠️ BLOQUEA.

    El SKU es el `default_code` del PRODUCTO, no el texto de la línea: el texto
    lo puede editar cualquiera en Odoo, el producto no. Una línea cuyo producto
    no tenga código sale con `sku = ""`, y quien empareja la trata como
    desconocida (no adivina). Las secciones y notas (`display_type`) no son
    mercancía y se saltan.
    """
    kw = kw or _kw
    ids = sorted({int(i) for i in order_ids if i})
    if not ids:
        return {}
    lineas = kw("sale.order.line", "search_read",
                [[["order_id", "in", ids]]],
                {"fields": ["order_id", "product_id", "product_uom_qty", "name",
                            "display_type"]}) or []
    pids = sorted({int(l["product_id"][0]) for l in lineas
                   if not l.get("display_type") and l.get("product_id")})
    codigos: dict[int, str] = {}
    if pids:
        for p in kw("product.product", "search_read", [[["id", "in", pids]]],
                    {"fields": ["default_code"],
                     "context": {"active_test": False}}) or []:
            codigos[int(p["id"])] = str(p.get("default_code") or "").strip()
    salida: dict[int, list[dict[str, Any]]] = {i: [] for i in ids}
    for l in lineas:
        if l.get("display_type"):
            continue
        oid = int((l.get("order_id") or [0])[0] or 0)
        pid = int((l.get("product_id") or [0])[0] or 0) if l.get("product_id") else 0
        sku = codigos.get(pid, "")
        titulo = str(l.get("name") or "").strip()
        # El nombre lo armamos como "[SKU] título del canal": se le quita el
        # prefijo para no repetir el SKU en pantalla.
        if sku and titulo.startswith(f"[{sku}]"):
            titulo = titulo[len(sku) + 2:].strip()
        try:
            cantidad = float(l.get("product_uom_qty") or 0)
        except (TypeError, ValueError):
            cantidad = 0.0
        salida.setdefault(oid, []).append({
            "sku": sku, "titulo": titulo[:300] or None,
            "cantidad": int(cantidad) if cantidad == int(cantidad) else cantidad})
    return salida


def _skus(lineas: list[dict[str, Any]] | None) -> list[str] | None:
    """Los SKUs de una parte, o None si NO se sabe con certeza (sin líneas, o
    alguna sin código). None nunca se confunde con "no lleva nada"."""
    if not lineas or any(not l.get("sku") for l in lineas):
        return None
    return sorted({l["sku"] for l in lineas})


def _cantidades(lineas: list[dict[str, Any]] | None) -> dict[str, float] | None:
    """{sku: piezas} de una parte (suma si el SKU sale en varias líneas), o None
    en los mismos casos que `_skus`. Hace falta para el SKU repartido entre
    partes: saber si UN paquete lleva todas sus piezas o sólo las de una."""
    if _skus(lineas) is None:
        return None
    salida: dict[str, float] = {}
    for l in lineas or []:
        try:
            n = float(l.get("cantidad") or 0)
        except (TypeError, ValueError):
            return None
        salida[l["sku"]] = salida.get(l["sku"], 0.0) + n
    return salida


def _agregar_partes(items: list[dict[str, Any]],
                    todas: dict[str, list[dict[str, Any]]],
                    ids_faltan: set[int],
                    divididas: set[str] | None = None) -> None:
    """Le cuelga `dividida` y `partes` a los renglones de venta partida.

    `divididas` son las ventas con DOS O MÁS órdenes vivas. Con `siempre_partes`
    entran aquí también las de una sola orden —para que el emparejador pueda
    trabajar con la misma forma de datos— y ésas salen con `dividida=False`:
    seguir el camino del surtido dividido con una sola parte sería mentirle al
    resumen. Sin el argumento, todas cuentan como divididas (los llamadores
    viejos sólo mandaban ésas).

    Si leer las líneas falla, las partes salen con `skus=None`: el refresco
    sólo podrá aplicar la regla del paquete único y lo demás lo deja para la
    vuelta siguiente. Una falla aquí NUNCA tumba la cola de las ventas que no
    están partidas.
    """
    ids = [int(o["id"]) for d in items for o in todas.get(d["order_id"], [])]
    try:
        por_orden: dict[int, list[dict[str, Any]]] | None = _lineas_de_ordenes(ids)
    except Exception as exc:  # noqa: BLE001
        log.warning("pendientes_de_guia: no se pudieron leer los productos de %d "
                    "orden(es) de surtido dividido (%s); se emparejan sin SKU",
                    len(ids), str(exc)[:160])
        por_orden = None
    for d in items:
        partes = []
        for o in sorted(todas.get(d["order_id"], []),
                        key=lambda x: (_num_parte(x["client_order_ref"]), x["id"])):
            partes.append({
                "sale_id": int(o["id"]),
                "nombre": o.get("name") or "",
                "ref": str(o["client_order_ref"]),
                "pickings": [i for i in (o.get("picking_ids") or []) if i in ids_faltan],
                "sin_pdf": bool(not o.get("meli_etiqueta_file")
                                and o.get("state") in _CONFIRMADAS),
                "skus": (_skus(por_orden.get(int(o["id"])))
                         if por_orden is not None else None),
                "cantidades": (_cantidades(por_orden.get(int(o["id"])))
                               if por_orden is not None else None),
            })
        d["dividida"] = (divididas is None or d["order_id"] in divididas)
        d["partes"] = partes


def emparejar_partes(partes: list[dict[str, Any]],
                     paquetes: list[dict[str, Any]],
                     skus_canal: list[str] | None = None) -> dict[int, dict[str, Any]]:
    """
    ¿Qué paquete le toca a cada parte de una venta partida? PURA: sin red.

    `partes`     = [{sale_id, skus: [..] | None, cantidades: {sku: n} | None}]
                   (de `pendientes_de_guia`)
    `paquetes`   = [{guia, skus: [..] | None, cantidades: {sku: n}, ...}] (del
                   canal; skus None = no se sabe qué lleva; un SKU que falta en
                   `cantidades` = no se sabe cuántas piezas). Ya fusionados.
    `skus_canal` = TODOS los SKUs de la venta según el canal, o None si no se
                   saben completos.

    Devuelve {sale_id: {"estado", "paquete", "motivo"}} con estado:
      · `asignada`    → ése es SU paquete (puede no tener guía todavía);
      · `sin_paquete` → sus productos no van en ningún paquete aún: esperar;
      · `ambigua`     → no se puede saber con certeza: NO se le escribe nada.

    LAS REGLAS (Brandon, 18-sep; la a, endurecida el mismo día):
      a) UN solo paquete a la vista → se le da a la parte que DEMOSTRADAMENTE
         va ahí: se sabe qué lleva el paquete, se sabe qué lleva la parte, y
         todos sus SKUs van dentro. "Sólo veo un paquete" NO es "la venta va en
         un paquete": la caja del otro almacén puede no haberse comprado aún.
         Por eso un paquete de contenido desconocido, o una parte de SKUs
         desconocidos, queda `ambigua`.
      b) VARIOS paquetes → cada parte recibe el que contiene SUS SKUs, y sólo si
         es exactamente uno y los contiene todos.
      c) Lo que no se pueda emparejar con certeza → nada. Nunca adivinar: la
         entrega no se vuelve a pisar, y una guía ajena manda la caja a otra
         parte.

    SKU REPARTIDO entre dos partes (pide 3, TEXCO tiene 2 y TEXCO II 1 → regla 3
    de `planear_almacenes`): que el SKU vaya en el paquete no dice de qué
    almacén salió la pieza. Con un solo paquete sólo cuenta si el paquete lleva
    TODAS las piezas de la venta de ese SKU (la cantidad que Temu y TikTok sí
    mandan); si no, o si no se sabe, `ambigua`. Con varios, siempre `ambigua`.

    SKU DISTINTO AL DEL CANAL (se vendió el padre y en Odoo va la variante; se
    cambió a mano): esa parte nunca va a cruzar con ningún paquete. Antes se
    quedaba "esperando" para siempre; ahora sale `ambigua` con su motivo, para
    que se vea en el resumen y alguien la ponga a mano.

    Con UNA sola parte no hay nada que repartir: el paquete es suyo, como en
    una venta sin partir.
    """
    def _conj(v: Any) -> set[str] | None:
        if v is None:
            return None
        s = {str(x).strip() for x in v if str(x or "").strip()}
        return s or None

    def _cant(v: Any) -> dict[str, float]:
        salida_c: dict[str, float] = {}
        if isinstance(v, dict):
            for k, n in v.items():
                try:
                    if str(k or "").strip() and n is not None:
                        salida_c[str(k).strip()] = float(n)
                except (TypeError, ValueError):
                    continue
        return salida_c

    varias = len(partes) > 1
    cuenta: dict[str, int] = {}
    # Piezas de cada SKU en TODA la venta (suma de las partes). None = no se sabe.
    total: dict[str, float | None] = {}
    for p in partes:
        cp = _cant(p.get("cantidades")) if p.get("cantidades") is not None else None
        for s in (_conj(p.get("skus")) or ()):
            cuenta[s] = cuenta.get(s, 0) + 1
            n = None if cp is None else cp.get(s)
            total[s] = None if (n is None or total.get(s, 0.0) is None) else total.get(s, 0.0) + n
    compartidos = {s for s, n in cuenta.items() if n > 1}
    todos = set(cuenta)
    canal = _conj(skus_canal)
    paqs = [(q, _conj(q.get("skus")), _cant(q.get("cantidades"))) for q in paquetes]

    def _no_cubre(cq: dict[str, float], skus: set[str]) -> list[str]:
        """Los SKUs repartidos de los que el paquete NO lleva todas las piezas."""
        return sorted(x for x in skus & compartidos
                      if total.get(x) is None or cq.get(x) is None or cq[x] < total[x])

    salida: dict[int, dict[str, Any]] = {}
    for p in partes:
        sid = int(p["sale_id"])
        s = _conj(p.get("skus"))
        if varias and s is not None and canal is not None and not s <= canal:
            salida[sid] = {"estado": "ambigua", "paquete": None,
                           "motivo": "SKU distinto al del canal: "
                                     + ", ".join(sorted(s - canal))}
            continue
        if not paqs:
            salida[sid] = {"estado": "sin_paquete", "paquete": None,
                           "motivo": "la venta no tiene paquete todavía"}
            continue
        if len(paqs) == 1:
            q, c, cq = paqs[0]
            if not varias:
                # Una sola orden: lo de siempre (el camino de una venta sin partir).
                if c is None or s is None or todos <= c or s <= c:
                    salida[sid] = {"estado": "asignada", "paquete": q,
                                   "motivo": "un solo paquete para toda la venta"}
                elif not (s & c):
                    salida[sid] = {"estado": "sin_paquete", "paquete": None,
                                   "motivo": "el único paquete no lleva sus SKUs"}
                else:
                    salida[sid] = {"estado": "ambigua", "paquete": None,
                                   "motivo": "el único paquete lleva sólo parte de sus SKUs"}
                continue
            if c is None:
                salida[sid] = {"estado": "ambigua", "paquete": None,
                               "motivo": "no se sabe qué lleva el único paquete a la vista"}
            elif s is None:
                salida[sid] = {"estado": "ambigua", "paquete": None,
                               "motivo": "no se sabe qué SKUs lleva la parte"}
            elif s <= c:
                faltan = _no_cubre(cq, s)
                if faltan:
                    salida[sid] = {"estado": "ambigua", "paquete": None,
                                   "motivo": "SKU repartido entre partes y el paquete no "
                                             "lleva todas sus piezas: " + ", ".join(faltan)}
                else:
                    salida[sid] = {"estado": "asignada", "paquete": q,
                                   "motivo": "sus SKUs van en el único paquete"}
            elif not (s & c):
                salida[sid] = {"estado": "sin_paquete", "paquete": None,
                               "motivo": "el único paquete no lleva sus SKUs"}
            else:
                salida[sid] = {"estado": "ambigua", "paquete": None,
                               "motivo": "el único paquete lleva sólo parte de sus SKUs"}
            continue
        paqs2 = [(q, c) for q, c, _cq in paqs]
        if any(c is None for _q, c in paqs2):
            salida[sid] = {"estado": "ambigua", "paquete": None,
                           "motivo": f"{len(paqs2)} paquetes y de alguno no se sabe qué lleva"}
            continue
        if s is None:
            salida[sid] = {"estado": "ambigua", "paquete": None,
                           "motivo": "no se sabe qué SKUs lleva la parte"}
            continue
        cands = [(q, c) for q, c in paqs2 if s & c]
        if not cands:
            salida[sid] = {"estado": "sin_paquete", "paquete": None,
                           "motivo": "ninguno de los paquetes lleva sus SKUs"}
        elif len(cands) > 1:
            salida[sid] = {"estado": "ambigua", "paquete": None,
                           "motivo": f"sus SKUs van en {len(cands)} paquetes"}
        elif s & compartidos:
            salida[sid] = {"estado": "ambigua", "paquete": None,
                           "motivo": "un SKU está repartido entre partes"}
        elif not s <= cands[0][1]:
            salida[sid] = {"estado": "ambigua", "paquete": None,
                           "motivo": "parte de sus SKUs no va en ningún paquete"}
        else:
            salida[sid] = {"estado": "asignada", "paquete": cands[0][0],
                           "motivo": "el único paquete con sus SKUs"}
    return salida


def guias_de_venta(paquetes: list[dict[str, Any]]) -> tuple[str, str]:
    """
    La guía de la VENTA para la bitácora: la guía si es una sola, "G1 + G2" si
    son varias, en el orden dado y sin repetir. La paquetería, igual.

    Quien llama pasa SÓLO los paquetes que quedaron puestos en Odoo, parte por
    parte (#1, #2…): la bitácora es lo que pinta el panel y lo que cruza el
    Excel del día; una guía que no llegó a ninguna entrega no va ahí.
    """
    orden = [q for q in paquetes if isinstance(q, dict)]
    guias: list[str] = []
    vistas: set[str] = set()
    paqs: list[str] = []
    for q in orden:
        g = str(q.get("guia") or "").strip()
        clave = "".join(g.split()).lower()
        if not g or clave in vistas:
            continue
        vistas.add(clave)
        guias.append(g)
        pq = str(q.get("paqueteria") or "").strip()
        if pq and pq not in paqs:
            paqs.append(pq)
    return " + ".join(guias), " + ".join(paqs)


def partes_de_ventas(canal: str, ventas: list[str],
                     timeout: float | None = None,
                     plazo_total: float | None = None) -> dict[str, list[dict[str, Any]]]:
    """
    Las órdenes de Odoo de cada venta, con lo que va en cada una. ⚠️ BLOQUEA.

    {venta: [{odoo_order_id, odoo_name, ref, parte, almacen, estado,
              lineas: [{sku, titulo, cantidad}], guia, paqueteria,
              tiene_pdf, pdf_nombre}]}

    POR QUÉ SE LE PREGUNTA A ODOO. La bitácora guarda UNA fila por venta —su
    llave es la venta— con `odoo_order_id` de la PRIMERA parte y las líneas de
    la venta sin decir a qué parte van. Lo que se partió vive en Odoo, y de ahí
    se lee al vuelo: sin migración, sin columnas nuevas.

    Pocas consultas por lote, sin importar cuántas ventas: órdenes (por
    `client_order_ref`, la venta exacta o `venta#n`), sus líneas, el código de
    sus productos y sus entregas de salida. El PDF se revisa con `bin_size` (el
    tamaño, no el archivo). `timeout` (segundos) acota cada llamada y
    `plazo_total` la lectura ENTERA: con Odoo lento, cuatro llamadas de 8 s por
    lote se volvían más de un minuto. Si se acaba el plazo, lanza `TimeoutError`
    y quien llama sigue sin partes.

    SÓLO LECTURA: `search_read`, nada más.
    """
    canal = (canal or "").lower()
    partner = _PARTNER.get(canal)
    if not partner:
        return {}
    unicas = list(dict.fromkeys(str(v).strip() for v in ventas if str(v or "").strip()))
    if not unicas:
        return {}
    limite_reloj = (time.monotonic() + float(plazo_total)) if plazo_total else None
    if timeout is None and limite_reloj is None:
        kw = _kw
    else:
        def kw(modelo: str, metodo: str, args: list, kwargs: dict | None = None) -> Any:
            t = timeout
            if limite_reloj is not None:
                resta = limite_reloj - time.monotonic()
                if resta <= 0.05:
                    raise TimeoutError("se acabó el plazo para leer las partes en Odoo")
                t = resta if t is None else min(float(t), resta)
            return odoo._kw_flujo(modelo, metodo, args, kwargs, timeout=t)

    salida: dict[str, list[dict[str, Any]]] = {}
    for i in range(0, len(unicas), 40):
        lote = unicas[i:i + 40]
        condiciones: list[Any] = []
        for v in lote:
            condiciones += [["client_order_ref", "=", v],
                            ["client_order_ref", "=like", f"{v}#%"]]
        dominio = ([["partner_id", "=", partner]]
                   + ["|"] * (len(condiciones) - 1) + condiciones)
        ordenes = kw("sale.order", "search_read", [dominio],
                     {"fields": ["name", "client_order_ref", "state", "warehouse_id",
                                 "picking_ids", "meli_etiqueta_file",
                                 "meli_etiqueta_filename"],
                      "context": {"bin_size": True}}) or []
        # `=like` trata `_` como comodín: se re-filtra por la forma exacta.
        pedidas = set(lote)
        propias = []
        for o in ordenes:
            ref = str(o.get("client_order_ref") or "")
            venta, _s, suf = ref.partition("#")
            if venta in pedidas and (not _s or suf.isdigit()):
                propias.append(o)
        if not propias:
            continue
        lineas = _lineas_de_ordenes([o["id"] for o in propias], kw)
        picks = sorted({p for o in propias for p in (o.get("picking_ids") or [])})
        salidas: dict[int, dict[str, Any]] = {}
        if picks:
            for p in kw("stock.picking", "search_read",
                        [[["id", "in", picks], ["picking_type_code", "=", "outgoing"]]],
                        {"fields": ["name", "state", "carrier_tracking_ref",
                                    "carrier_id"]}) or []:
                salidas[int(p["id"])] = p
        for o in propias:
            vivas = [salidas[p] for p in (o.get("picking_ids") or [])
                     if p in salidas and salidas[p].get("state") != "cancel"]
            guias = list(dict.fromkeys(
                str(p.get("carrier_tracking_ref") or "").strip() for p in vivas
                if str(p.get("carrier_tracking_ref") or "").strip()))
            paqs = list(dict.fromkeys(
                str((p.get("carrier_id") or [None, ""])[1] or "").strip() for p in vivas
                if p.get("carrier_id")))
            ref = str(o.get("client_order_ref") or "")
            salida.setdefault(ref.partition("#")[0], []).append({
                "odoo_order_id": int(o["id"]),
                "odoo_name": o.get("name") or "",
                "ref": ref,
                "parte": _num_parte(ref) or 1,
                "almacen": (o.get("warehouse_id") or [None, None])[1],
                "estado": o.get("state"),
                "lineas": lineas.get(int(o["id"]), []),
                "guia": " + ".join(guias),
                "paqueteria": " + ".join(p for p in paqs if p),
                "tiene_pdf": bool(o.get("meli_etiqueta_file")),
                "pdf_nombre": o.get("meli_etiqueta_filename") or None,
            })
    for v in salida:
        salida[v].sort(key=lambda p: (_num_parte(p["ref"]), p["odoo_order_id"]))
    return salida


def fijar_guia(canal: str, order_id: str, guia: str,
               pickings: list[int] | None = None) -> dict[str, Any]:
    """
    Escribe el número de rastreo en las entregas de salida y lo VERIFICA. ⚠️ BLOQUEA.

    LA GUÍA (el número) VA EN LA ENTREGA: `stock.picking.carrier_tracking_ref`
    es el campo de la casa —10,381 entregas ya lo usan, incluidas todas las de
    Mercado Libre—. El PDF va aparte, en la orden: ver `fijar_etiqueta`.

    SE ESCRIBE DESPUÉS DE CONFIRMAR, y no es un parche: la guía la asigna la
    paquetería cuando se compra el envío, y eso pasa con la orden ya viva.
    Esperar la guía para confirmar sería un círculo cerrado.

    "ASEGURARSE DE QUE SE SUBIÓ" (Brandon, 11-sep): tras escribir se RE-LEE, y
    sólo es `ok` si Odoo devuelve la guía al volver a preguntarle. Un `write`
    que contesta bien no prueba que el dato quedó.

    RESPETA LOS INTERRUPTORES, igual que `crear_orden` y `cancelar_orden`: si
    alguien aprieta "Apagar todo" en la pestaña, esto también se detiene. Un
    botón de pánico que no apaga todo no es un botón de pánico.

    `carrier_id` NO se toca: exige un `delivery.carrier` dado de alta, y
    adivinar cuál corresponde a "J&T express" escribiría un dato falso en un
    campo que la gente usa para filtrar.
    """
    canal = (canal or "").lower()
    if not guia:
        return {"ok": False, "accion": "sin_guia"}
    if not habilitado():
        return {"ok": False, "accion": "apagado"}
    if not canal_activo(canal):
        return {"ok": False, "accion": "canal_apagado"}
    if canal not in _PARTNER:
        return {"ok": False, "accion": "canal_desconocido"}
    try:
        if not pickings:
            return {"ok": False, "accion": "sin_entregas"}
        # Se re-lee justo antes de escribir: entre que se armó la cola y ahora,
        # alguien pudo ponerla a mano. Ese valor gana siempre.
        vivas = _kw("stock.picking", "search_read",
                    [[["id", "in", list(pickings)],
                      ["picking_type_code", "=", "outgoing"],
                      ["state", "!=", "cancel"],
                      ["carrier_tracking_ref", "in", [False, ""]]]],
                    {"fields": ["id"]})
        objetivo = [p["id"] for p in vivas]
        if not objetivo:
            return {"ok": False, "accion": "ya_tenia", "escritas": 0}
        _kw("stock.picking", "write", [objetivo, {"carrier_tracking_ref": guia}])
        leidas = _kw("stock.picking", "read", [objetivo, ["carrier_tracking_ref"]])
        verificada = bool(leidas) and all(
            (p.get("carrier_tracking_ref") or "") == guia for p in leidas)
        if verificada:
            log.info("Odoo %s: guía %s escrita y verificada en %d entrega(s) de la venta %s",
                     canal, guia, len(objetivo), order_id)
        else:
            log.warning("Odoo %s: la guía %s de %s NO quedó al re-leer: %s",
                        canal, guia, order_id, leidas)
        return {"ok": verificada, "accion": "escrita" if verificada else "no_verificada",
                "escritas": len(objetivo), "verificada": verificada}
    except Exception as exc:  # noqa: BLE001 — nunca rompe el refresco
        log.warning("Odoo %s: no se pudo escribir la guía de %s: %s",
                    canal, order_id, exc)
        return {"ok": False, "accion": "error", "motivo": str(exc)[:200]}


def fijar_etiqueta(canal: str, order_id: str, sale_ids: list[int],
                   pdf: bytes, nombre: str) -> dict[str, Any]:
    """
    Sube el PDF de la etiqueta a "Subir guía" de la orden y lo VERIFICA. ⚠️ BLOQUEA.

    El campo es `sale.order.meli_etiqueta_file` (+ `meli_etiqueta_filename`),
    que es lo que ya llenan las órdenes de SHEIN: el PDF nombrado como la guía.

    NO PISA: si la orden ya tiene archivo —alguien lo subió a mano, u otra
    vuelta ya lo hizo— se deja. Y sólo toca órdenes del partner del canal: un
    id que no fuera de Temu no recibe una etiqueta de Temu.

    VERIFICADO: tras escribir se re-lee con `bin_size`; sólo es `ok` si Odoo
    reporta el archivo y el nombre al volver a preguntarle.

    RESPETA LOS INTERRUPTORES, como `crear_orden`, `cancelar_orden` y
    `fijar_guia`.
    """
    canal = (canal or "").lower()
    if not pdf or not pdf.startswith(b"%PDF"):
        return {"ok": False, "accion": "sin_pdf"}
    if not habilitado():
        return {"ok": False, "accion": "apagado"}
    if not canal_activo(canal):
        return {"ok": False, "accion": "canal_apagado"}
    partner = _PARTNER.get(canal)
    if not partner:
        return {"ok": False, "accion": "canal_desconocido"}
    if not sale_ids:
        return {"ok": False, "accion": "sin_ordenes"}
    try:
        actuales = _kw("sale.order", "read",
                       [list(sale_ids), ["meli_etiqueta_file", "partner_id", "state"]],
                       {"context": {"bin_size": True}})
        propias = [o for o in actuales
                   if not o.get("meli_etiqueta_file")
                   and (o.get("partner_id") or [None])[0] == partner]
        # Se re-lee el estado justo antes de escribir: sólo CONFIRMADAS.
        objetivo = [o["id"] for o in propias if o.get("state") in _CONFIRMADAS]
        if not objetivo:
            accion = "sin_confirmar" if propias else "ya_tenia"
            return {"ok": False, "accion": accion, "subidas": 0}
        _kw("sale.order", "write",
            [objetivo, {"meli_etiqueta_file": base64.b64encode(pdf).decode("ascii"),
                        "meli_etiqueta_filename": nombre}])
        leidas = _kw("sale.order", "read",
                     [objetivo, ["meli_etiqueta_file", "meli_etiqueta_filename"]],
                     {"context": {"bin_size": True}})
        verificada = bool(leidas) and all(
            o.get("meli_etiqueta_file") and o.get("meli_etiqueta_filename") == nombre
            for o in leidas)
        if verificada:
            log.info("Odoo %s: etiqueta %s subida y verificada en %d orden(es) de %s (%s)",
                     canal, nombre, len(objetivo), order_id,
                     ", ".join(str(o.get("meli_etiqueta_file")) for o in leidas))
        else:
            log.warning("Odoo %s: la etiqueta %s de %s NO quedó al re-leer: %s",
                        canal, nombre, order_id, leidas)
        return {"ok": verificada, "accion": "subida" if verificada else "no_verificada",
                "subidas": len(objetivo), "verificada": verificada}
    except Exception as exc:  # noqa: BLE001 — nunca rompe el refresco
        log.warning("Odoo %s: no se pudo subir la etiqueta de %s: %s",
                    canal, order_id, exc)
        return {"ok": False, "accion": "error", "motivo": str(exc)[:200]}


def buscar_por_ref(canal: str, order_id: str) -> dict[str, Any] | None:
    """
    ¿Ya existe la orden de esta venta? Se busca por `client_order_ref` Y por
    partner: el ref solo es único DE HECHO (Odoo no le pone restricción), así
    que acotar por el partner del canal cierra cualquier choque entre ids de
    marketplaces distintos.
    """
    partner = _PARTNER.get(canal)
    dominio: list = [["client_order_ref", "=", str(order_id)]]
    if partner:
        dominio.append(["partner_id", "=", partner])
    filas = _kw("sale.order", "search_read", [dominio],
                {"fields": ["name", "state", "amount_total", "warehouse_id"],
                 "limit": 1})
    return filas[0] if filas else None


# ── Crear / cancelar ────────────────────────────────────────────────────────

def crear_orden(canal: str, order_id: str, fecha: str | None,
                items: list[dict[str, Any]],
                confirmar: bool | None = None,
                dry_run: bool = False,
                esperar_guia: bool | None = None) -> dict[str, Any]:
    """
    La orden de venta en Odoo. Idempotente por `client_order_ref`.

    `items` = [{sku, cantidad, precio_unitario, titulo}] — lo que ya trae
    normalizado `pedidos_tiktok`/`pedidos_temu`.

    `dry_run=True` calcula TODO —producto, almacén, foto de stock— y no escribe,
    sin importar en qué escalón estén las banderas. Es lo que hace seguro al
    endpoint `/simular`: sin este parámetro, "simular" dejaría de simular en
    cuanto alguien apagara `SOLO_REGISTRO`, y el que lo llamara para mirar
    estaría creando órdenes de verdad.

    `esperar_guia` decide si esta venta sólo deja su ESPACIO (`None` = pregunta
    al switch del canal, que es lo que hace el camino de una venta). El trabajo
    de guías pasa `False` cuando ya tiene la guía en la mano y viene a crear de
    verdad: sin eso, la venta volvería a diferirse y la orden no nacería jamás.

    NUNCA lanza: la llama el camino de una venta, y una venta no se puede caer
    porque Odoo no contestó. Un pedido sin orden en Odoo se repara; una venta
    perdida, no.
    """
    # EL INTERRUPTOR SE LEE AQUÍ, ya dentro del hilo, no en el seam: es una
    # consulta a kubera que BLOQUEA, y el seam corre en la corrutina de la venta
    # (regla 11). `dry_run` lo salta a propósito — simular tiene que funcionar
    # con la automatización apagada, que es justo cuando se quiere simular.
    #
    # ⚠️ APAGADO **NO ES SALIR CORRIENDO**, y esto costó datos. La primera
    # versión devolvía aquí mismo, antes de resolver productos y almacén, así
    # que la bitácora guardaba una fila hueca: sin almacén, sin cobertura y —lo
    # grave— **sin la foto de stock**. Medido el 1-sep: 29 ventas registradas
    # entre el 29-ago y el 1-sep, las 30 líneas con `stock_libre` en NULL. Esa
    # foto es el único dato irrecuperable de toda la tabla; veinte minutos
    # después ya no se puede reconstruir.
    #
    # Ahora apagado se comporta como el modo observación: calcula TODO y no
    # escribe en Odoo. Cuesta dos o tres llamadas XML-RPC por venta (~6 al día
    # en TikTok), y a cambio la fila sirve para algo y no se pierde nada.
    apagado_general = not dry_run and not habilitado()
    # El canal se decide AQUÍ por lo mismo que el general: es una lectura a
    # kubera y bloquea. El seam solo pre-filtra con `_CANALES_POSIBLES`.
    apagado_canal = not dry_run and not apagado_general and not canal_activo(canal)

    if confirmar is None:
        confirmar = bool(getattr(settings, "odoo_ventas_confirmar", False))
    solo_registro_cfg = bool(getattr(settings, "odoo_ventas_solo_registro", True))

    # ESPERAR LA GUÍA es el escalón de MÁS ARRIBA de la escalera, y por eso se
    # pregunta al final: sólo difiere lo que de verdad se iba a crear. Con el
    # general apagado, el canal apagado o SOLO_REGISTRO encendido no hay nada
    # que diferir, y decir "espera guía" ahí escondería el motivo real detrás de
    # uno nuevo. El orden del `accion` de abajo repite esta misma prioridad.
    espera = False
    if (not dry_run and not apagado_general and not apagado_canal
            and not solo_registro_cfg):
        espera = (espera_guia_activa(canal) if esperar_guia is None
                  else bool(esperar_guia))
    solo_registro = (dry_run or apagado_general or apagado_canal or espera
                     or solo_registro_cfg)
    partner = _PARTNER.get(canal)
    if not partner:
        return {"ok": False, "motivo": f"canal '{canal}' sin partner configurado"}

    try:
        # 1 · Idempotencia ANTES de nada.
        previa = buscar_por_ref(canal, order_id)
        if previa:
            return {"ok": True, "accion": "ya_existia", "odoo_id": previa["id"],
                    "nombre": previa["name"], "estado": previa["state"],
                    "almacen": (previa.get("warehouse_id") or [None, None])[1]}

        # 2 · Los SKUs, todos o ninguno. Una orden a medias hace que el almacén
        #     surta incompleto sin enterarse — peor que no tener orden.
        pedidos_sku = [(i.get("sku") or "").strip() for i in items]
        prods = productos_por_sku(pedidos_sku)
        faltan = [s for s in pedidos_sku if s and s not in prods]
        if faltan:
            return {"ok": False, "accion": "sku_sin_producto", "skus_faltantes": faltan,
                    "motivo": f"sin producto en Odoo: {', '.join(faltan)}"}
        if not prods:
            return {"ok": False, "motivo": "la venta no trae ningún SKU legible"}

        lineas = [{"product_id": prods[(i["sku"] or "").strip()]["id"],
                   "sku": (i["sku"] or "").strip(),
                   "cantidad": int(i.get("cantidad") or 1),
                   "precio": float(i.get("precio_unitario") or 0),
                   "titulo": i.get("titulo") or ""}
                  for i in items if (i.get("sku") or "").strip()]

        # 3 · El plan de almacenes. Puede salir MÁS DE UNA parte: ver
        #     `planear_almacenes` para las tres reglas.
        libres = libre_por_almacen([l["product_id"] for l in lineas])
        plan = planear_almacenes(lineas, libres)
        partes = plan["partes"]

        def _payload(parte: dict, ref: str) -> dict:
            v = {
                "partner_id": partner,
                "warehouse_id": parte["almacen_id"],
                "client_order_ref": ref,          # ← la llave de idempotencia
                "origin": f"{_ETIQUETA.get(canal, canal)} {order_id}",
                "note": (f"Creada automáticamente desde {_ETIQUETA.get(canal, canal)} "
                         f"(orden {order_id}). Panel Omnicanal."
                         + (f" Surtido dividido: parte desde {parte['almacen']}."
                            if len(partes) > 1 else "")),
                "order_line": [(0, 0, {
                    "product_id": l["product_id"],
                    "product_uom_qty": l["cantidad"],
                    "price_unit": l["precio"],
                    "name": (f"[{l['sku']}] {l['titulo']}"[:400] or l["sku"]),
                }) for l in parte["lineas"]],
            }
            # La fecha de la VENTA, no la de captura: si no, la contabilidad y
            # cualquier reporte por día quedan corridos.
            if fecha:
                try:
                    v["date_order"] = (datetime.fromisoformat(str(fecha))
                                       .astimezone(timezone.utc)
                                       .strftime("%Y-%m-%d %H:%M:%S"))
                except Exception:  # noqa: BLE001
                    pass
            return v

        # LA REFERENCIA CUANDO SE PARTE. Con una sola parte se conserva el id a
        # secas —así las órdenes viejas siguen encontrándose—; al dividir, cada
        # parte lleva su propio sufijo. Si las dos llevaran el mismo ref, la
        # idempotencia encontraría la primera y NUNCA crearía la segunda: media
        # venta se quedaría sin surtir, en silencio.
        refs = [str(order_id) if len(partes) == 1 else f"{order_id}#{i}"
                for i in range(1, len(partes) + 1)]

        if solo_registro:
            # Modo observación: se calculó TODO —producto, almacenes, foto de
            # stock, payloads— y no se escribe.
            accion = ("simulado" if dry_run
                      else "apagado" if apagado_general
                      else "canal_apagado" if apagado_canal
                      else "espera_guia" if espera
                      else "solo_registro")
            motivos = {
                "apagado": "el interruptor general está apagado: se midió el "
                           "stock y no se escribió en Odoo",
                "canal_apagado": f"el canal {canal} está apagado: se midió el "
                                 "stock y no se escribió en Odoo",
                # NO es un error y la pantalla no debe pintarlo como tal: es el
                # ESPACIO de la orden. La orden nace cuando el canal entregue la
                # guía, y entonces esta misma fila se rellena.
                "espera_guia": "esperando la guía del canal para crear la orden "
                               "en Odoo: se midió el stock y no se escribió nada",
            }
            return {"ok": True, "accion": accion, "canal": canal,
                    "motivo": motivos.get(accion),
                    "order_id": order_id,
                    "almacen": " + ".join(p["almacen"] for p in partes),
                    "almacen_id": partes[0]["almacen_id"],
                    "cobertura": plan["cobertura"], "faltante": plan["faltante"],
                    "stock_foto": plan["stock_foto"],
                    "payload": [_payload(p, r) for p, r in zip(partes, refs)],
                    "partes": [{"almacen": p["almacen"],
                                "lineas": [{"sku": l["sku"], "cantidad": l["cantidad"]}
                                           for l in p["lineas"]]} for p in partes],
                    "lineas": [{"sku": l["sku"], "cantidad": l["cantidad"],
                                "precio": l["precio"]} for l in lineas]}

        # 4 · Crear. UNA orden por parte, cada una con su propio candado: si la
        #     segunda falla, la primera ya quedó y el reintento solo crea la que
        #     falta (su ref todavía no existe).
        creadas: list[dict[str, Any]] = []
        for parte, ref in zip(partes, refs):
            previa = buscar_por_ref(canal, ref)
            if previa:
                creadas.append({"odoo_id": previa["id"], "nombre": previa["name"],
                                "estado": previa["state"], "almacen": parte["almacen"],
                                "ya_existia": True})
                continue
            oid = _kw("sale.order", "create", [_payload(parte, ref)])
            leida = _kw("sale.order", "read",
                        [[oid], ["name", "state", "amount_total"]])[0]
            # 5 · Confirmar (aquí es donde Odoo RESERVA y `free_qty` baja).
            if confirmar:
                _kw("sale.order", "action_confirm", [[oid]])
                leida = _kw("sale.order", "read",
                            [[oid], ["name", "state", "amount_total"]])[0]
            creadas.append({"odoo_id": oid, "nombre": leida["name"],
                            "estado": leida["state"], "total": leida["amount_total"],
                            "almacen": parte["almacen"], "ya_existia": False})

        # 6 · ¿QUÉ PASÓ DE VERDAD? No se dice "confirmada" porque se PIDIÓ
        #     confirmar: se dice porque Odoo la dejó en `sale`. Es la misma
        #     lección que `cancelar_orden` ya tenía escrita —`action_cancel` no
        #     siempre cancela y contesta igual—, y aquí faltaba: una orden que
        #     se queda en borrador NO reserva, así que `free_qty` no baja y el
        #     stock sigue ofreciéndose. Reportarla como confirmada esconde
        #     justo la sobreventa que confirmar venía a evitar.
        sin_confirmar = [c for c in creadas
                         if not c["ya_existia"] and c["estado"] != "sale"]
        motivo = None
        if all(c["ya_existia"] for c in creadas):
            accion = "ya_existia"
        elif confirmar and sin_confirmar:
            accion = "no_se_pudo_confirmar"
            motivo = ("Odoo dejó en '%s' a %s: no reserva, el stock se sigue "
                      "ofreciendo. Hay que confirmarla a mano."
                      % (sin_confirmar[0]["estado"],
                         ", ".join(c["nombre"] for c in sin_confirmar)))
            log.warning("Odoo %s: venta %s — %s", canal, order_id, motivo)
        else:
            accion = "confirmada" if confirmar else "creada"
        log.info("Odoo %s: venta %s → %s en %s (cobertura %s, %s)", canal, order_id,
                 ", ".join(c["nombre"] for c in creadas),
                 " + ".join(c["almacen"] for c in creadas), plan["cobertura"], accion)

        # La bitácora guarda UNA fila por venta (su llave es la venta), así que
        # con surtido dividido los nombres van juntos: "S37010 + S37011". Se
        # prefiere eso a inventar una fila por parte, que rompería la llave.
        return {"ok": not sin_confirmar, "accion": accion, "motivo": motivo,
                "odoo_id": creadas[0]["odoo_id"],
                "nombre": " + ".join(c["nombre"] for c in creadas),
                "estado": creadas[0]["estado"],
                "total": sum(float(c.get("total") or 0) for c in creadas),
                "canal": canal, "order_id": order_id,
                "almacen": " + ".join(c["almacen"] for c in creadas),
                "almacen_id": partes[0]["almacen_id"],
                "cobertura": plan["cobertura"], "faltante": plan["faltante"],
                "stock_foto": plan["stock_foto"],
                "ordenes": creadas,
                "lineas": [{"sku": l["sku"], "cantidad": l["cantidad"],
                            "precio": l["precio"]} for l in lineas]}
    except Exception as exc:  # noqa: BLE001 — jamás rompe la venta
        log.exception("odoo_ventas.crear_orden(%s, %s) falló", canal, order_id)
        return {"ok": False, "motivo": str(exc)[:300], "canal": canal,
                "order_id": order_id}


# ── Creación diferida: la orden nace cuando aparece la guía ─────────────────
#
# EL BLOQUEADOR QUE HUBO QUE RESOLVER. `pendientes_de_guia` le pregunta a ODOO
# quién espera guía, y una venta sin orden no existe para Odoo: la cola se
# quedaría vacía y nadie crearía nada. Así que la cola se INVIERTE — la parte de
# las ventas sin orden sale de la bitácora (`ops.odoo_sale_orders`).
#
# ⚠️ Y LA CONDICIÓN DE SALIDA DE ESA COLA ES UN HECHO DE ODOO
# (`odoo_order_id is not null`), NUNCA la columna `guia`. Es exactamente la
# lección que ya está escrita en `pendientes_de_guia`: la primera versión de la
# cola de guías elegía por esa columna, que el seam de la venta rellena en
# cualquier re-aviso SIN tocar Odoo, y las filas salían de la cola con la
# entrega vacía PARA SIEMPRE. Aquí sería peor: saldrían sin orden, y la venta
# nunca llegaría al almacén.

def cola_de_guias(canal: str, dias: int = 14,
                  limite: int = 60) -> list[dict[str, Any]]:
    """
    La cola COMPLETA del trabajo de guías. ⚠️ BLOQUEA: llamar desde un hilo.

    Dos orígenes, y por eso son dos consultas y no una:
      · las ventas que YA tienen orden en Odoo y a las que les falta el número
        o el PDF (`pendientes_de_guia`, la de siempre);
      · las que sólo tienen su ESPACIO —`accion='espera_guia'` y sin
        `odoo_order_id`— y cuya orden hay que CREAR en cuanto haya guía
        (`odoo_ventas_log.pendientes_sin_orden`), marcadas con `espera_guia`.

    Las que esperan van PRIMERO: son las que todavía no le han dicho nada al
    almacén. Si una venta apareciera en las dos —la bitácora no alcanzó a
    anotar el `odoo_order_id`— gana la de Odoo, que es el hecho; la
    idempotencia de `crear_orden` la cubre de todos modos.

    LOS DOS FALLOS NO SON SIMÉTRICOS, a propósito:
      · si la BITÁCORA no contesta, se sigue con la cola de siempre. Perder las
        que esperan sólo aplaza su orden una vuelta, y parar por eso dejaría sin
        guía a las que ya tienen orden;
      · si ODOO no contesta, esto LANZA y el trabajo entero se detiene con su
        error a la vista. Sin Odoo no se puede crear ni escribir nada, así que
        seguir sólo gastaría cuota del canal y llenaría el resumen de fallos
        que no dicen nada. Los dos trabajos ya lo atrapan y reportan
        `error: cola: …`; es el comportamiento que tenían antes de esto.
    """
    canal = (canal or "").lower()
    espera: list[dict[str, Any]] = []
    try:
        from services import odoo_ventas_log
        # ⚠️ `dias` MANDA SÓLO SOBRE LA COLA DE ODOO. La mitad de espera usa
        # `_dias_espera()`, que es la MISMA ventana que `stock_watch` usa para
        # decidir cuánto stock esconder. Antes aquí se pasaba el `dias` del job,
        # así que `TEMU_GUIAS_DIAS` —una variable de rendimiento que nadie
        # relacionaría con inventario— movía la ventana de creación y la
        # desalineaba de la del inventario: a la baja, días de mercancía
        # escondida sin nadie que creara la orden; al alza, piezas devueltas al
        # anaquel mientras el trabajo todavía las crearía. Ver `_dias_espera`.
        espera = odoo_ventas_log.pendientes_sin_orden(
            canal, dias=_dias_espera(), limite=_limite_espera(limite))
    except Exception as exc:  # noqa: BLE001
        log.warning("cola_de_guias(%s): la bitácora no contestó (%s); sólo van las "
                    "que ya tienen orden", canal, str(exc)[:150])
    con_orden = pendientes_de_guia(canal, dias, limite)
    ya = {str(d["order_id"]) for d in con_orden}
    espera = [d for d in espera if str(d["order_id"]) not in ya]

    # EL LÍMITE ES DE LA VUELTA, NO DE CADA ORIGEN. Sumar dos colas de `limite`
    # duplicaría el trabajo por vuelta sin que nadie lo hubiera pedido. Y se
    # reparte a MEDIAS en vez de dar prioridad a uno: con la cola de espera por
    # delante, un atasco de ventas por crear dejaría sin guía a las órdenes que
    # ya existen —el almacén las tiene impresas y esperando el número— y al
    # revés, una cola larga de guías retrasaría para siempre las ventas que el
    # almacén todavía no ve. Cada mitad que sobra se la queda la otra.
    tope = max(1, int(limite))
    if len(espera) + len(con_orden) > tope:
        mitad = tope // 2
        n_esp = min(len(espera), max(mitad, tope - len(con_orden)))
        espera, con_orden = espera[:n_esp], con_orden[:tope - n_esp]
    return espera + con_orden


def mantener_espera(canal: str, forzar: bool = False) -> dict[str, Any]:
    """
    El mantenimiento de la cola de espera, antes de cada vuelta del trabajo de
    guías. ⚠️ BLOQUEA: llamar desde un hilo. Nunca lanza. **No escribe en Odoo.**

    Tres cosas, y cada una tapa una forma distinta de perder una venta:

    1 · REPONER LOS ESPACIOS QUE NO SE ESCRIBIERON. Con la creación diferida, la
        fila de la bitácora dejó de ser una bitácora y pasó a ser el ÚNICO
        camino por el que una venta llega a Odoo — y falla en silencio
        (`registrar` devuelve False y el seam se lo traga). Un tropiezo de dos
        minutos de kubera y esas ventas no existen para nadie: ni orden, ni
        espacio, ni resta de stock, y el sondeo no vuelve a pasar por ellas.
        Aquí se vuelve a mirar `channel.orders` y se les crea el espacio que les
        faltaba. Se pasa `esperar_guia=True` EXPLÍCITO: esta reposición no puede
        escribir en Odoo ni por accidente.

    2 · CADUCAR lo que ya no va a resolver, para que no se esfume en silencio al
        cumplir la ventana (ver `odoo_ventas_log.caducar_esperas`).

    3 · VINCULAR lo que alguien creó a mano. Bajo el régimen diferido esto dejó
        de ser un extra: es lo que apaga la resta de stock cuando la orden
        aparece por un camino que no es el nuestro, y lo que repara la fila
        cuando la bitácora no se enteró de una orden que sí nació. Por eso corre
        aquí, dentro del trabajo que sí está encendido, y no sólo detrás de
        `ODOO_VENTAS_VINCULAR_ENABLED`.

    Todo esto sólo corre cuando el canal está de verdad en régimen diferido.
    `forzar=True` es el DRENAJE A MANO (el trabajo de guías está apagado y hay
    cola huérfana): hace 2 y 3 —que son reparaciones y siempre son correctas—
    pero NO 1, porque con el régimen apagado una venta nueva ya se crea al
    vender y reponerle un "espacio" sería inventar una espera que nadie pidió.
    """
    r: dict[str, Any] = {"canal": canal, "espacios_repuestos": 0,
                         "espacios_fallidos": 0, "caducadas": 0, "vinculadas": 0}
    activo = espera_guia_activa(canal)
    if not activo and not forzar:
        return {**r, "nota": "el canal no espera la guía: no hay nada que mantener"}
    from services import odoo_ventas_log

    dias = _dias_espera()
    # 1 · los espacios que no quedaron escritos
    faltan: list[dict[str, Any]] = []
    if activo:
        try:
            horas = max(1, int(getattr(settings, "odoo_ventas_espera_repone_h", 48) or 48))
            faltan = odoo_ventas_log.ventas_sin_fila(canal, horas=horas)
        except Exception as exc:  # noqa: BLE001
            faltan = []
            r["error_reponer"] = str(exc)[:150]
            log.warning("mantener_espera(%s): no se pudo mirar channel.orders: %s",
                        canal, str(exc)[:150])
    for v in faltan:
        try:
            fecha = v["creado_at"].isoformat() if v.get("creado_at") else None
            res = crear_orden(canal, str(v["order_id"]), fecha,
                              list(v.get("items") or []), esperar_guia=True)
            if odoo_ventas_log.registrar(canal, v["cuenta"], str(v["order_id"]),
                                         res, list(v.get("items") or [])):
                r["espacios_repuestos"] += 1
            else:
                r["espacios_fallidos"] += 1
        except Exception as exc:  # noqa: BLE001 — una mala no detiene las demás
            r["espacios_fallidos"] += 1
            log.warning("mantener_espera(%s): no se pudo reponer el espacio de "
                        "%s: %s", canal, v.get("order_id"), str(exc)[:150])
    if r["espacios_repuestos"] or r["espacios_fallidos"]:
        log.warning("Odoo %s: %s venta(s) no tenían ni su ESPACIO en la bitácora "
                    "(kubera no contestó al venderse). Repuestos: %s, fallidos: %s.",
                    canal, len(faltan), r["espacios_repuestos"], r["espacios_fallidos"])
        _avisar_espacios_perdidos(canal, len(faltan), r["espacios_fallidos"])

    # 2 · las que se pasaron de la ventana
    try:
        r["caducadas"] = int(odoo_ventas_log.caducar_esperas(
            canal, dias=dias).get("caducadas") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("mantener_espera(%s): caducar falló: %s", canal, str(exc)[:150])

    # 3 · las que alguien creó a mano
    try:
        r["vinculadas"] = int(odoo_ventas_log.vincular_sin_orden(
            canal, dias=max(dias, 30)).get("vinculadas") or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("mantener_espera(%s): vincular falló: %s", canal, str(exc)[:150])
    return r


def _avisar_espacios_perdidos(canal: str, vistas: int, fallidos: int) -> None:
    """La campana cuando hubo ventas sin espacio. Nunca lanza."""
    try:
        from services import alertas
        alertas.avisar(
            f"odoo_espacio_perdido:{canal}",
            f"{vistas} venta(s) de {canal} se registraron SIN su espacio en la "
            f"bitácora (kubera no contestó en el instante de la venta). Sin esa "
            f"fila no hay orden en Odoo, no se ven en /automatizacion y su stock "
            f"se vuelve a ofrecer. Repuestas: {vistas - fallidos}; "
            f"todavía sin espacio: {fallidos}.")
    except Exception as exc:  # noqa: BLE001
        log.debug("_avisar_espacios_perdidos: %s", exc)


def drenar_espera(canal: str, limite: int = 60) -> dict[str, Any]:
    """
    Crea las órdenes de la cola de espera AUNQUE el trabajo de guías esté
    apagado. ⚠️ BLOQUEA. Nunca lanza.

    Es la salida a mano del caso que deja huérfanas: se apaga
    `TEMU_GUIAS_ENABLED`/`TIKTOK_GUIAS_ENABLED` con ventas ya esperando, las
    nuevas vuelven a crearse al vender (falla cerrado) y las que estaban en la
    cola no las retoma NADIE. Hasta ahora la única salida era volver a encender
    el trabajo entero.

    Devuelve la cola para que el que llama —el endpoint— la recorra con el
    canal en la mano: aquí no se sabe pedir una guía a Temu ni a TikTok.
    """
    from services import odoo_ventas_log
    try:
        cola = odoo_ventas_log.pendientes_sin_orden(
            canal, dias=_dias_espera(), limite=_limite_espera(limite))
    except Exception as exc:  # noqa: BLE001
        return {"canal": canal, "error": str(exc)[:200], "pendientes": 0}
    return {"canal": canal, "pendientes": len(cola), "cola": cola}


def crear_con_guia(canal: str, cuenta: str, order_id: str, fecha: str | None,
                   items: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Ahora que la venta tiene guía: crea la orden, la confirma, y devuelve su
    renglón de cola para escribirle el número y el PDF EN LA MISMA VUELTA.
    ⚠️ BLOQUEA: llamar desde un hilo. Nunca lanza.

    EL PLAN DE ALMACENES SE RECALCULA, no se reusa. `crear_orden` vuelve a
    preguntar `free_qty` y a correr `planear_almacenes` con el stock de HOY, y
    eso es el punto: entre la venta y la guía pasan uno o dos días (Temu:
    mediana 28.4 h), y el almacén que cubría entonces puede no cubrir ahora —o
    al revés, una recepción puede haber vuelto innecesario partir la venta.
    Surtir por la foto de anteayer es surtir de un almacén vacío.

    La foto VIEJA no se pierde ni se pisa: vive en `ops.odoo_sale_order_items.
    stock_libre` (la del instante de la venta, el único dato irrecuperable de la
    tabla) y la bitácora nunca la re-escribe. Lo que sí se actualiza es la
    DECISIÓN —almacén, cobertura, total—, porque la de la fila era la de un plan
    que no llegó a ejecutarse. Si el plan cambió, se dice en el motivo.

    Devuelve `{ok, accion, resultado, cola, motivo}`. `cola` es el renglón de
    `cola_de_una_venta` (o None si a la orden ya no le falta nada).
    """
    from services import odoo_ventas_log

    antes = {}
    try:
        antes = odoo_ventas_log.plan_guardado(canal, cuenta, order_id) or {}
    except Exception as exc:  # noqa: BLE001 — sólo sirve para redactar el motivo
        log.debug("crear_con_guia: no se pudo leer el plan guardado de %s (%s)",
                  order_id, exc)

    r = crear_orden(canal, str(order_id), fecha, items, esperar_guia=False)
    if not r.get("odoo_id"):
        # No nació. Se deja la fila como está —sigue en espera— y la vuelta
        # siguiente lo reintenta: la cola se vacía por el HECHO de que exista la
        # orden, así que un fallo nunca la saca. Sólo se anota el tropiezo.
        motivo = (r.get("motivo") or r.get("accion")
                  or "Odoo no creó la orden y no dijo por qué")
        log.warning("Odoo %s: la venta %s ya tiene guía pero la orden NO se creó "
                    "(%s): %s", canal, order_id, r.get("accion"), motivo)
        try:
            odoo_ventas_log.anotar_intento(canal, cuenta, order_id, motivo)
        except Exception as exc:  # noqa: BLE001
            log.debug("crear_con_guia: no se pudo anotar el intento (%s)", exc)
        return {"ok": False, "accion": r.get("accion") or "error",
                "resultado": r, "cola": None, "motivo": motivo}

    if r.get("accion") == "ya_existia":
        # Alguien la creó a mano entre la venta y ahora (o `vincular_sin_orden`
        # no llegó primero). No hay plan nuevo que contar y no se pisa el viejo:
        # `crear_orden` sale por idempotencia antes de calcular nada.
        #
        # PERO puede estar en BORRADOR —la capturaron y no la confirmaron— y un
        # borrador NO reserva. Se intenta confirmar aquí, una vez: si no, la
        # fila saldría de la resta de stock (ya tiene `odoo_order_id`) sin que
        # Odoo hubiera apartado una sola pieza.
        r = _confirmar_si_borrador(canal, order_id, r)
        cambio = "La orden ya existía en Odoo: sólo se vincula."
    else:
        cambio = _cambio_de_plan(antes, r)
    horas = _horas_desde(antes.get("creado_at"))
    # ⚠️ ORDEN EXISTE ≠ ORDEN RESERVA. `crear_orden` devuelve
    # `no_se_pudo_confirmar` CON `odoo_id` cuando Odoo la dejó en borrador. Eso
    # no es una creación lograda: no reserva, el stock se sigue ofreciendo y hay
    # que volver a intentarlo. Se escribe el id igual —el hecho de que la orden
    # existe no se puede perder, y `piezas_sin_orden` sigue restándole las
    # piezas por su ACCIÓN, no por el id— pero se devuelve `ok=False` para que
    # el trabajo lo cuente como intento fallido y no como `creadas`.
    sin_reservar = r.get("accion") == odoo_ventas_log.ACCION_SIN_RESERVA
    r = dict(r, motivo=(("Orden creada al aparecer la guía"
                         if not sin_reservar else
                         "Orden creada al aparecer la guía pero SIN CONFIRMAR "
                         "(no reserva)")
                        + (f", {horas:.1f} h después de la venta" if horas else "")
                        + ". " + cambio
                        + (" " + (r.get("motivo") or "") if sin_reservar else "")))
    anotada = False
    try:
        anotada = bool(odoo_ventas_log.registrar(canal, cuenta, str(order_id), r,
                                                 items, refrescar_plan=True))
    except Exception as exc:  # noqa: BLE001 — la orden ya existe; eso es lo que no se pierde
        log.warning("crear_con_guia: la bitácora de %s no se pudo actualizar: %s",
                    order_id, str(exc)[:150])
    if not anotada:
        # EL PEOR CRUCE, y no se cura solo. La orden nació (Odoo YA reservó) y
        # la bitácora no se enteró: la fila se queda `espera_guia` con
        # `odoo_order_id` NULL y `piezas_sin_orden` sigue restando sus piezas
        # ENCIMA de la reserva. No es una pasada: es cada 20 minutos hasta que
        # caduque o alguien corra `vincular_sin_orden` (que está detrás de otra
        # bandera y puede estar apagada). Con los SKUs sin holgura eso es dejar
        # de vender. Así que se reintenta el UPDATE MÍNIMO —cuatro columnas,
        # ninguna línea—, que tiene mucha más probabilidad de pasar.
        try:
            if odoo_ventas_log.fijar_orden(
                    canal, cuenta, str(order_id), int(r["odoo_id"]),
                    nombre=str(r.get("nombre") or ""),
                    estado=str(r.get("estado") or ""),
                    accion=str(r.get("accion") or ""),
                    motivo=str(r.get("motivo") or "")):
                log.warning("crear_con_guia: la bitácora de %s no aceptó el "
                            "registro completo, pero SÍ el id de la orden (%s): "
                            "la resta de stock deja de duplicarse.",
                            order_id, r.get("odoo_id"))
            else:
                log.error("crear_con_guia: la orden %s de la venta %s existe en "
                          "Odoo y la bitácora NO lo sabe. Mientras siga así, su "
                          "stock se descuenta DOS veces (reserva + resta).",
                          r.get("nombre"), order_id)
        except Exception as exc:  # noqa: BLE001
            log.error("crear_con_guia: ni el id de la orden de %s se pudo "
                      "anotar (%s)", order_id, str(exc)[:150])

    cola = None
    try:
        cola = cola_de_una_venta(canal, str(order_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("crear_con_guia: no se pudo armar el renglón de cola de %s: %s",
                    order_id, str(exc)[:150])
    log.info("Odoo %s: venta %s → %s creada AL APARECER LA GUÍA (%s). %s",
             canal, order_id, r.get("nombre"), r.get("accion"), cambio)
    if sin_reservar:
        log.warning("Odoo %s: la orden %s de la venta %s se quedó EN BORRADOR: no "
                    "reserva. Se cuenta como intento fallido y se reintenta la "
                    "vuelta siguiente; su stock se sigue restando mientras tanto.",
                    canal, r.get("nombre"), order_id)
    return {"ok": not sin_reservar, "accion": r.get("accion"), "resultado": r,
            "cola": cola, "motivo": r.get("motivo")}


def _confirmar_si_borrador(canal: str, order_id: str,
                           r: dict[str, Any]) -> dict[str, Any]:
    """
    Una orden que YA EXISTÍA pero está en `draft` no reserva: se intenta
    confirmar una vez y se re-lee. ⚠️ BLOQUEA. Nunca lanza.

    Se RE-LEE a propósito, no se supone: `action_confirm` contesta igual cuando
    no mueve nada — la misma lección que `cancelar_orden` ya tenía escrita. Si
    sigue en borrador, la acción pasa a `no_se_pudo_confirmar` y con eso
    `piezas_sin_orden` le sigue restando las piezas y el panel la pide a mano.
    """
    if str(r.get("estado") or "") != "draft":
        return r
    if not bool(getattr(settings, "odoo_ventas_confirmar", False)):
        return r
    oid = int(r["odoo_id"])
    try:
        _kw("sale.order", "action_confirm", [[oid]])
        leida = _kw("sale.order", "read", [[oid], ["name", "state"]])[0]
    except Exception as exc:  # noqa: BLE001
        log.warning("crear_con_guia: no se pudo confirmar el borrador %s de %s: %s",
                    oid, order_id, str(exc)[:150])
        leida = {"state": "draft"}
    if leida.get("state") == "sale":
        log.info("Odoo %s: la orden %s de %s estaba en borrador y se confirmó.",
                 canal, r.get("nombre"), order_id)
        return dict(r, estado="sale", accion="confirmada")
    return dict(r, estado=leida.get("state") or "draft",
                accion="no_se_pudo_confirmar",
                motivo=(f"La orden ya existía en '{leida.get('state')}' y no se "
                        "pudo confirmar: NO reserva, el stock se sigue ofreciendo."))


def _cambio_de_plan(antes: dict[str, Any], ahora: dict[str, Any]) -> str:
    """Una frase que dice si el almacén de hoy es el del día de la venta."""
    viejo = str(antes.get("almacen") or "").strip()
    nuevo = str(ahora.get("almacen") or "").strip()
    cob_v = str(antes.get("cobertura") or "").strip()
    cob_n = str(ahora.get("cobertura") or "").strip()
    if not viejo:
        return f"Almacén: {nuevo or '—'} (cobertura {cob_n or '—'})."
    if viejo == nuevo and cob_v == cob_n:
        return (f"Plan de almacenes recalculado con stock de hoy: igual que el día "
                f"de la venta ({nuevo}, cobertura {cob_n or '—'}).")
    return (f"Plan de almacenes recalculado con stock de hoy: CAMBIÓ — el día de "
            f"la venta era {viejo} (cobertura {cob_v or '—'}) y hoy es "
            f"{nuevo or '—'} (cobertura {cob_n or '—'}).")


def _horas_desde(cuando: Any) -> float | None:
    """Horas entre `cuando` (datetime de la bitácora) y ahora. None si no se sabe."""
    if not isinstance(cuando, datetime):
        return None
    ref = cuando if cuando.tzinfo else cuando.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - ref).total_seconds() / 3600.0)


def notar_combinados(canal: str, dias: int = 21, limite: int = 300,
                     dry_run: bool = False) -> dict[str, Any]:
    """
    "Esta orden viaja en la MISMA caja que…" escrito en Odoo. ⚠️ BLOQUEA.

    Vive en `services/odoo_notas_combinado.py` —este archivo ya pasa de 1,300
    líneas— y se re-exporta aquí porque es parte de la misma familia: lee las
    mismas órdenes, usa el mismo `_kw` y obedece los mismos interruptores que
    `fijar_guia` y `fijar_etiqueta`. Ver allá el porqué de cada decisión.
    """
    from services import odoo_notas_combinado
    return odoo_notas_combinado.notar_combinados(canal, dias=dias, limite=limite,
                                                 dry_run=dry_run)


def cancelar_orden(canal: str, order_id: str) -> dict[str, Any]:
    """
    El marketplace canceló: la orden de Odoo se cancela también.

    No es cosmético. De las ventas de TikTok que se revisaron, la MAYORÍA
    terminó cancelada; una orden viva por una venta muerta deja al almacén
    surtiendo lo que nadie compró y —con "Odoo descuenta"— deja la reserva
    mordiendo stock que sí se podía vender.
    """
    if not habilitado():
        return {"ok": False, "accion": "apagado",
                "motivo": "el interruptor de órdenes en Odoo está apagado"}
    try:
        previa = buscar_por_ref(canal, order_id)
        if not previa:
            return {"ok": False, "accion": "sin_orden",
                    "motivo": "no hay orden en Odoo para esa venta"}
        if previa["state"] == "cancel":
            return {"ok": True, "accion": "ya_cancelada", "odoo_id": previa["id"],
                    "nombre": previa["name"]}
        if bool(getattr(settings, "odoo_ventas_solo_registro", True)):
            return {"ok": True, "accion": "solo_registro_cancelar",
                    "odoo_id": previa["id"], "nombre": previa["name"]}
        # ⚠️ `action_cancel` NO SIEMPRE CANCELA, y contesta igual (auditoría
        # 28-ago). Este Odoo es 17.0+e, y ahí una orden confirmada con entrega
        # en `done` o factura publicada NO se cancela: el método devuelve la
        # ACCIÓN del asistente `sale.order.cancel` —que existe en esta base— y
        # queda esperando a un humano. La versión anterior devolvía
        # "cancelada" sin volver a leer nada, así que reportaba como hecho algo
        # que no había pasado: el peor modo de fallo posible para una
        # cancelación, porque nadie va a ir a revisarla.
        #
        # `disable_cancel_warning` salta el asistente cuando se puede; y el
        # estado se RELEE siempre, que es lo único que de verdad lo prueba
        # (`crear_orden` ya releía tras `action_confirm`; esto faltaba).
        _kw("sale.order", "action_cancel", [[previa["id"]]],
            {"context": {"disable_cancel_warning": True}})
        despues = _kw("sale.order", "read", [[previa["id"]], ["state", "name"]])[0]
        if despues["state"] != "cancel":
            log.warning("Odoo %s: orden %s NO se canceló (quedó en '%s') — venta %s",
                        canal, previa["name"], despues["state"], order_id)
            return {"ok": False, "accion": "no_se_pudo_cancelar",
                    "odoo_id": previa["id"], "nombre": previa["name"],
                    "estado": despues["state"],
                    "motivo": f"Odoo la dejó en '{despues['state']}': "
                              "probablemente tiene entrega hecha o factura "
                              "publicada y hay que cancelarla a mano"}
        log.info("Odoo %s: orden %s CANCELADA (venta %s)",
                 canal, previa["name"], order_id)
        return {"ok": True, "accion": "cancelada", "odoo_id": previa["id"],
                "nombre": previa["name"], "estado": "cancel"}
    except Exception as exc:  # noqa: BLE001
        log.exception("odoo_ventas.cancelar_orden(%s, %s) falló", canal, order_id)
        return {"ok": False, "motivo": str(exc)[:300]}
