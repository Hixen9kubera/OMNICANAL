"""
ordenes_venta.py — Las ÓRDENES DE VENTA PROPIAS del panel (Inventario → Órdenes
de venta, folio OV-00001…) contra el esquema de Eduardo: migración 0064
(`ventas.ov_*`, `almacen.almacenes`) y 0065 (`almacen.stock_almacen`, `almacen.stock_mov`).
El contrato que este archivo cumple está en
docs/MIGRACION_0064_0065_GUIA_AGENTE.md (§3 lo que garantiza la base, §4 lo que
le toca al código); los patrones SQL salen de backend/scripts/verificar_0064_0065.py.

ES EL ÚNICO ESCRITOR de `ventas.ov_*` (y uno de los dos de `almacen.stock_almacen` /
`almacen.stock_mov`: el otro será `inventario_libro`). El router y el barrido de
cancelaciones sólo LLAMAN estas funciones. De aquí no sale nada hacia afuera:
ni Odoo, ni WooCommerce, ni un marketplace. Lo único externo es el bucket
privado de los PDF (`ov_storage`), y siempre FUERA de la transacción.

EL MODELO (plan v3 de Eduardo, 5-oct-2026)
  · La orden sólo existe en BODEGAS DE KUBERA (`almacen.almacenes`, fuente kubera y
    `admite_ov`): hoy ENSAYO. La bodega va POR RENGLÓN.
  · Confirmar APARTA todo o nada contra `almacen.stock_almacen` (libre = fisico −
    apartado). Si un renglón no alcanza, no se confirma nada y se dice cuál.
  · Fuera de borrador el CONTENIDO es inmutable (lo impone un trigger). Un error
    en una confirmada se resuelve cancelando o borrando, no editando.
  · Entregar es por renglón y una sola vez por renglón, con piezas 0..cantidad;
    cada pieza que sale queda en el libro (`almacen.stock_mov`, motivo salida_ov).
  · Si salió alguna pieza, una cancelación ya no es `cancelada`: es
    `entregada_cancelada`, la que espera devolución.

POR QUÉ ESTÁ ESCRITO ASÍ (lo que no es gusto)

1. UNA TRANSICIÓN = UN `execute`. Estado, saldo, libro, renglones y mensaje de
   bitácora viajan en un solo WITH que termina en `ops.exigir(...)`, y los
   `set local lock_timeout / statement_timeout` van en ese mismo envío. Una
   sentencia es atómica; dos sentencias detrás del pool no lo son (SteadyDB
   puede repetir un `execute` en OTRA conexión). Por eso el SQL de cada
   transición es una CONSTANTE de este módulo (`SQL_*`) y no se arma a pedazos.

2. EL ORDEN DE LOS CANDADOS ES SIEMPRE EL MISMO (guía §4.3): la fila guardia de
   la orden → `almacen.almacenes` FOR SHARE → `almacen.stock_almacen` FOR UPDATE en
   orden (sku, almacen), en una CTE `materialized` que se lee después →
   escrituras → `ops.exigir`. `crear_auto` es la excepción documentada (toma el
   folio al final: sólo sube si todo alcanzó). Un interbloqueo aquí es un bug.

3. LOS ERRORES SE DISTINGUEN POR CÓDIGO, NUNCA POR EL TEXTO: `pgcode` y
   `diag.constraint_name`; el único mensaje que se lee es el de `KB001`, que por
   contrato ES el motivo (`clasificar`). Hacia la persona sale SIEMPRE un texto
   en español de este archivo (`TEXTO_MOTIVO`), jamás el nombre técnico a secas
   ni el texto de Postgres (trae host, usuario y valores).

4. LOS ERRORES DIFERIDOS SALEN AL COMMIT, no en el `execute`: por eso cada
   transición cierra su `with sdb.get_cursor()` ANTES de dar nada por hecho, y
   nada externo (Storage) depende de una sentencia que aún no confirmó.

5. `rev` ES EL CANDADO OPTIMISTA DE LAS PERSONAS; los procesos (el canal) usan
   compare-and-set POR ESTADO y aguantan ver la misma cancelación dos veces.
   Y un reintento propio no es un conflicto: cada transición deja una marca
   (`datos.op`) en su mensaje; si el CAS falla y mi marca ya está, es mi
   escritura que sí entró (la conexión murió al contestar el COMMIT).

6. LOS PERMISOS SE DECIDEN AQUÍ (dependen del ESTADO de la orden; el RBAC por
   prefijo no lo ve). `permisos()` es pura y cada operación la vuelve a exigir.
   QUIÉN entra siempre por el parámetro `quien` y se escribe explícito: nunca
   `''` ni `None`, y la `via` sólo del catálogo (panel | api | claude | automatico).

7. LAS BANDERAS SON FILAS (`ops.automatizacion_flags`), con la variable de
   entorno sólo de respaldo y en `false`. Si la fila no se puede LEER, la
   bandera vale APAGADA. Y si faltan las tablas (la migración no está en esa
   base), nada truena: se pregunta `to_regclass` con caché y se dice.

CÓMO SE PRUEBA (guía §7.3): cada función pública acepta `cur=None`. Sin él abre
su propia transacción corta del pool (`sdb.get_cursor()`, que confirma al
salir). Con él usa ESE cursor —el de una prueba que termina en ROLLBACK— y no
confirma nada. El cursor prestado se protege con un SAVEPOINT por paso, para
que un rechazo esperado no deje abortada la transacción de quien lo prestó.
(Sólo para pruebas y conexiones propias: detrás del pool un savepoint no es
seguro, y por eso el camino de producción no usa ninguno.)

TODO ES SÍNCRONO (psycopg2 bloquea): el router lo llama con `asyncio.to_thread`
(regla 11). Y regla 13: aquí no hay ni un `SET` de sesión, sólo `set local`.

`null` significa «no lo sabemos» y NUNCA se colapsa a 0: un SKU sin fila de
saldo en su bodega (`libre` null) no es un SKU agotado, aunque para apartar
valga lo mismo (falla cerrado).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Callable
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extensions

from config import settings
from services import ov_storage
from services import supabase_db as sdb
from services.odoo_ventas_log import ACCIONES_CANCELADA_CANAL

log = logging.getLogger("omnicanal.ordenes_venta")

_ZONA = ZoneInfo("America/Mexico_City")

ESTADOS = ("borrador", "confirmada", "entregada", "cancelada", "entregada_cancelada")
VIAS = ("panel", "api", "claude", "automatico")
# Las llaves de `conteos` (tipo FiltroEstado de tipos.ts), en el orden de la pantalla.
FILTROS = ("todas", *ESTADOS, "por_devolver", "borradas")
# Los CHECK de la 0064 que Python valida ANTES, para contestar con palabras.
CANALES = ("temu", "tiktok", "mercado_libre", "amazon", "walmart", "shein", "directa", "otro")
TIPOS_ARCHIVO = ("comprobante", "factura", "envio_full")
ORIGENES_CANCELACION = ("manual", "sistema", "marketplace")
# El catálogo CERRADO de ventas.ov_mensajes.evento (ov_mensajes_evento_chk).
EVENTOS = ("creada", "borrador_guardado", "descartada", "confirmada", "no_alcanzo",
           "entregada_parcial", "entregada", "cancelada", "borrada_admin", "canal_cancelo",
           "devolucion_esperada", "devolucion_recibida", "devolucion_aprobada",
           "devolucion_merma", "devolucion_cerrada")

BANDERA_MODULO = "ordenes_venta"            # respaldo: ORDENES_VENTA_ENABLED (false)
BANDERA_AUTO = "ov_generacion_auto"         # sin variable de respaldo: sin fila, apagada
BUCKET = ov_storage.BUCKET

MAX_RENGLONES = 200
MAX_CANTIDAD = 100_000
MAX_PRECIO = Decimal("9999999.99")
MAX_IMPORTE = Decimal("999999999999.99")          # lo que cabe en numeric(14,2)
MAX_PDF = 15 * 1024 * 1024                        # el tope que tendrá el bucket
MAX_MENSAJE = 4000                                # CHECK de ventas.ov_mensajes.cuerpo
MAX_MOTIVO = 500
MAX_CLAVE = 80                                    # idempotencia del alta y del chat
MIN_MOTIVO_CANCELAR = 5                           # ov_ordenes_canc_m_chk
MIN_MOTIVO_BORRAR = 10                            # ov_ordenes_borrada_m_chk

# Topes de los textos del encabezado. `canal` y `mp_canal` se guardan en
# minúsculas y `mp_cuenta` en MAYÚSCULAS (ov_ordenes_mp_forma_chk): son
# identificadores, y el índice único de la venta compara el texto tal cual.
_TEXTOS = {"cliente": 120, "canal": 40, "mp_canal": 40, "mp_cuenta": 80, "mp_orden": 80,
           "descripcion": 500, "guia": 80, "paqueteria": 80}
_EN_MINUSCULAS = frozenset({"canal", "mp_canal"})
_EN_MAYUSCULAS = frozenset({"mp_cuenta"})
_FECHAS = ("fecha_venta", "entrega_limite")
# TODO lo que un BORRADOR deja editar del encabezado. Son las ÚNICAS columnas
# que `SQL_GUARDAR` nombra (los nombres jamás salen del cuerpo de la petición).
_CAMPOS = (*_TEXTOS, *_FECHAS, "moneda", "total", "comision", "precio_origen")
_MP = ("mp_canal", "mp_cuenta", "mp_orden")
_ETIQUETA = {"cliente": "cliente", "canal": "canal", "mp_canal": "canal de la venta",
             "mp_cuenta": "cuenta", "mp_orden": "orden de marketplace",
             "descripcion": "descripción", "guia": "guía", "paqueteria": "paquetería",
             "fecha_venta": "fecha de venta", "entrega_limite": "entrega límite",
             "moneda": "moneda", "total": "total", "comision": "comisión",
             "precio_origen": "origen del precio"}
_ROTULO = {"borrador": "en borrador", "confirmada": "confirmada", "entregada": "entregada",
           "cancelada": "cancelada", "entregada_cancelada": "entregada y cancelada"}

_MSG_MIGRACION = ("Faltan las migraciones 0064, 0065 y 0068 (órdenes de venta e inventario de "
                  "kubera, ya en los esquemas ventas y almacen) en esta base.")
_MSG_APAGADO = ("Las órdenes de venta están en modo prueba (la bandera «ordenes_venta» está "
                "apagada): sólo borradores, sin confirmar ni entregar.")
_MSG_AUTO_APAGADA = ("La generación automática de órdenes de venta está apagada (bandera "
                     "«ov_generacion_auto»).")
_MSG_CAMBIO = "La orden cambió mientras tanto; se recargó."
_MSG_NO_APLICA = "La operación ya no aplica al estado actual de la orden; se recargó."
_MSG_SIN_BASE = "kubera no contesta; intenta de nuevo en un momento."
_MSG_FULL = "Esa venta es FULL: sale del almacén del marketplace y no lleva orden propia."
_MSG_OCUPADO = "La bodega está ocupada con otro movimiento; intenta de nuevo en unos segundos."
_MSG_INESPERADO = "No se pudo completar la operación; quedó registrado para revisarlo."
_MSG_SIN_BUCKET = ("Todavía no se pueden adjuntar PDF: falta crear el bucket «ordenes-venta» "
                   "en Storage.")
_MSG_SIN_FISICO = ("No hay piezas físicas suficientes en la bodega para registrar esa salida "
                   "(un conteo dejó menos de lo apartado). Pide un conteo o entrega menos piezas.")
_MSG_ESPERA_SALIO = ("El canal canceló esta venta con el paquete en camino: primero hay que "
                     "contestar si salió.")
_MSG_YA_CONFIRMADA = ("La orden se confirmó mientras tanto: su contenido ya no cambia. "
                      "Se recargó.")

# Los motivos de KB001 (`ops.exigir`) DE NEGOCIO, dichos para una persona. Los
# nombres son los del verificador (guía §4.6); los de sentencias propias de este
# módulo siguen la misma forma. Lo que no está aquí sale como `_MSG_NO_APLICA`:
# nunca el nombre técnico.
TEXTO_MOTIVO = {
    "ov_no_esta_en_borrador_o_cambio_rev": _MSG_CAMBIO,
    "ov_no_esta_confirmada_o_cambio_rev": _MSG_CAMBIO,
    "ov_no_cancelable_o_cambio_rev": _MSG_CAMBIO,
    "ov_no_borrable_o_cambio_rev": _MSG_CAMBIO,
    "canal_cancelo_no_aplica": _MSG_CAMBIO,
    "canal_cancelo_entregada_no_aplica": _MSG_CAMBIO,
    "renglon_sin_plan_o_sin_saldo": ("No se pudo apartar: algún renglón no tiene bodega, o su "
                                     "bodega no tiene existencias registradas de ese SKU. "
                                     "No se apartó nada."),
    "no_alcanzo": "No alcanzó el stock para apartar. No se apartó nada.",
    "entrega_no_cuadra": ("Algún renglón ya había salido o cambió mientras tanto; "
                          "se recargó la orden."),
    "salio_no_cuadra": "La orden ya no está esperando la respuesta de «¿salió?»; se recargó.",
    "salio_tarde_no_cuadra": ("La orden ya no está cancelada, o no le quedan renglones por "
                              "registrar como salidos; se recargó."),
    "ov_borrada_o_no_existe": "La orden se borró mientras tanto.",
    "archivo_ya_no_esta": "Ese PDF ya no está: alguien lo quitó mientras tanto.",
}


def texto_de_motivo(motivo: str | None) -> str:
    """El motivo de un KB001 de negocio, en palabras. Nunca devuelve el nombre técnico."""
    return TEXTO_MOTIVO.get(motivo or "", _MSG_NO_APLICA)


# ══════════════════════════════════════════════════════════════════════════════
# Quién, y los errores (cada uno ya sabe su código HTTP)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Quien:
    """Quién pide. Lo arma el router; aquí sólo se lee y se escribe."""
    actor: str      # correo | 'servicio' | 'automatico'
    nombre: str     # visible: nombre de core.usuarios, 'API', 'Claude', 'Automático'
    via: str        # panel | api | claude | automatico
    rol: str        # admin | operador | lectura | ''

    @property
    def admin(self) -> bool:
        return self.rol == "admin"

    @property
    def escribe(self) -> bool:
        return self.rol in ("admin", "operador")


AUTOMATICO = Quien("automatico", "Automático", "automatico", "admin")


class ErrorOV(Exception):
    """Base. `.status` es el código HTTP y `str(exc)` el mensaje legible en español."""
    status = 500
    mensaje = "No se pudo completar la operación."

    def __init__(self, mensaje: str | None = None, status: int | None = None) -> None:
        super().__init__(mensaje or self.mensaje)
        if status is not None:
            self.status = status


class Invalido(ErrorOV):
    """Los datos no pasan la validación, o la orden no está en el estado que la acción pide."""
    status = 400


class SinPermiso(ErrorOV):
    """El rol de quien pide no alcanza. El mensaje dice por qué."""
    status = 403


class NoExiste(ErrorOV):
    status = 404


class Conflicto(ErrorOV):
    """La orden cambió (rev vieja), no alcanzó el stock, o esa venta ya tiene orden."""
    status = 409
    mensaje = _MSG_CAMBIO


class FaltaMigracion(ErrorOV):
    status = 409
    mensaje = _MSG_MIGRACION


class Apagado(ErrorOV):
    status = 409
    mensaje = _MSG_APAGADO


class Grande(ErrorOV):
    """El PDF pasa del tope del bucket."""
    status = 413


class FallaStorage(ErrorOV):
    """Storage no contestó o contestó mal: no es culpa de quien pide."""
    status = 502


class SinBase(ErrorOV):
    """kubera no contestó (falló la CONEXIÓN, no la sentencia). Es un 502 con un
    mensaje fijo: el texto de psycopg2 trae el host del pooler y el usuario, y
    con un DSN mal escrito hasta la contraseña. El detalle va al log."""
    status = 502
    mensaje = _MSG_SIN_BASE


# ══════════════════════════════════════════════════════════════════════════════
# El clasificador de errores de la base (guía §4.6 y §4.9, al final)
# ══════════════════════════════════════════════════════════════════════════════

# 23505 que significa «eso ya está»: releer y devolver lo que existe.
YA_EXISTIA = frozenset({"devoluciones_recepcion_uq", "devoluciones_retiro_uq",
                        "devoluciones_clave_uq", "stock_mov_clave_uq", "stock_mov_salida_ov_uq",
                        "stock_formato_hash_uq", "ov_archivos_vivo_uq"})
# …y los dos que SÓLO lo significan en un alta. En el «¿salió?» tardío el mismo
# 23505 es lo contrario: la venta ya tiene OTRA orden viva y la salida no se escribió.
YA_EXISTIA_ALTA = frozenset({"ov_ordenes_clave_uq", "ov_ordenes_mp_uq"})
ALTAS = frozenset({"crear_borrador", "crear_auto"})
# KB001 que NO son de negocio: la sentencia no escribió lo que debía. Son bugs.
KB001_QUE_AVISAN = frozenset({"renglones_no_cuadran", "cancelar_no_cuadra",
                              "devolucion_no_cuadra", "regla_de_negocio"})


def _pgcode(exc: BaseException) -> str | None:
    return getattr(exc, "pgcode", None)


def _diag(exc: BaseException, campo: str) -> str | None:
    return getattr(getattr(exc, "diag", None), campo, None)


def clasificar(exc: BaseException, operacion: str) -> tuple[str, str | None]:
    """(clase, detalle) de un error de Postgres. Recibe la OPERACIÓN porque el
    mismo 23505 es éxito en un alta y conflicto en una salida tardía.

      negocio     KB001 con su motivo: 409 con palabras, SIN alerta.
      ya_existia  único repetido que significa «ya está»: releer y devolverlo.
      ocupado     venció lock_timeout o statement_timeout: un reintento.
      deadlock    se rompió el orden de candados: un reintento, y avisa.
      inesperado  lo demás (invariantes, CHECK, FK, NOT NULL…): registrar y avisar.
    """
    codigo = _pgcode(exc)
    nombre = _diag(exc, "constraint_name")
    if codigo == "KB001":
        motivo = (_diag(exc, "message_primary") or "").strip() or "regla_de_negocio"
        if motivo in KB001_QUE_AVISAN or motivo.endswith("_escrituras_no_cuadran"):
            return "inesperado", motivo
        return "negocio", motivo
    if codigo == "23505" and (nombre in YA_EXISTIA
                              or (nombre in YA_EXISTIA_ALTA and operacion in ALTAS)):
        return "ya_existia", nombre
    if codigo in ("55P03", "57014"):
        return "ocupado", None
    if codigo == "40P01":
        return "deadlock", None
    return "inesperado", nombre


class _Rechazo(Exception):
    """La base rechazó una transición y ya está CLASIFICADA. No sale del módulo:
    cada operación decide qué hace con ella (releer, traducir, avisar)."""

    def __init__(self, clase: str, detalle: str | None, exc: BaseException) -> None:
        super().__init__(f"{clase}:{detalle}")
        self.clase = clase
        self.detalle = detalle
        self.pgcode = _pgcode(exc)
        self.exc = exc

    def es(self, pgcode: str, *nombres: str) -> bool:
        return self.pgcode == pgcode and (not nombres or self.detalle in nombres)


# Rechazos que NO son un bug de la sentencia sino algo que la persona puede
# resolver (guía §4.6, «23514 que sí son de usuario» y las excepciones de 42501
# y 23503). (pgcode, constraint) → (clase de error, mensaje).
_DE_USUARIO: dict[tuple[str, str], tuple[type[ErrorOV], str]] = {
    ("23503", "ov_lineas_almacen_fk"): (
        Invalido, "Esa bodega no es de kubera: las órdenes de venta sólo salen de bodegas propias."),
    ("23503", "ov_lineas_orden_fk"): (Invalido, "La orden ya no existe."),
    ("23503", "ov_mensajes_orden_fk"): (Invalido, "La orden ya no existe."),
    ("23503", "ov_archivos_orden_fk"): (Invalido, "La orden ya no existe."),
    ("42501", "ov_lineas_inmutable"): (Conflicto, _MSG_YA_CONFIRMADA),
    ("23514", "ov_ordenes_canal_cancelo_chk"): (Conflicto, _MSG_ESPERA_SALIO),
    ("23514", "stock_almacen_fisico_chk"): (Conflicto, _MSG_SIN_FISICO),
    ("23514", "stock_mov_saldo_chk"): (Conflicto, _MSG_SIN_FISICO),
    ("23514", "ov_ordenes_conf_chk"): (
        Invalido, "Esa orden nunca estuvo confirmada: no pudo haber salido de la bodega."),
}


def _error_de_rechazo(r: _Rechazo, operacion: str) -> ErrorOV:
    """Lo que sale HACIA AFUERA de un rechazo que la operación no trató aparte."""
    if r.clase == "negocio":
        return Conflicto(texto_de_motivo(r.detalle))
    if r.clase == "ya_existia":
        # Ese hecho ya estaba escrito (p. ej. la salida de ese renglón): lo normal
        # es que antes llegue un KB001 por estado o rev. Se relee.
        return Conflicto(_MSG_CAMBIO)
    de_usuario = _DE_USUARIO.get((r.pgcode or "", r.detalle or ""))
    if de_usuario:
        clase, texto = de_usuario
        return clase(texto)
    # Invariante rota, CHECK o FK que Python debió validar, NOT NULL de un autor…
    # Es un bug nuestro: va al log CON su código y su regla (lo que hace falta
    # para encontrarlo) y hacia afuera un 502 sin detalle.
    log.error("ordenes_venta.%s: la base rechazó la sentencia (pgcode=%s regla=%s columna=%s): %s",
              operacion, r.pgcode, r.detalle, _diag(r.exc, "column_name"),
              (str(r.exc).strip().splitlines() or [""])[0][:300])
    return ErrorOV(_MSG_INESPERADO, status=502)


# ══════════════════════════════════════════════════════════════════════════════
# La base: una unidad de trabajo corta, con el cursor propio o el prestado
# ══════════════════════════════════════════════════════════════════════════════

# Guía §4.7 (C3): con 6 conexiones en el pool, una espera larga de candado tumba
# el backend. Van como SET LOCAL en el MISMO envío que la sentencia: mueren con
# la transacción y no dejan estado en la conexión compartida del pooler.
_ENVOLTURA = "set local lock_timeout = '4s';\nset local statement_timeout = '15s';\n"


def _una(sql: str) -> str:
    """El texto COMPLETO de un `execute`: los dos `set local` y la sentencia."""
    return _ENVOLTURA + sql.strip() + "\n"


def _sin_tabla(exc: BaseException) -> bool:
    """42P01 = tabla que no existe; 42703 = columna que no existe. Manda el CÓDIGO,
    no el texto: un `savepoint … does not exist` (3B001) también dice «does not
    exist» y confundirlo con la falta de migración escondía una conexión muerta."""
    return _pgcode(exc) in ("42P01", "42703")


# ── kubera caída ──────────────────────────────────────────────────────────────
# La pestaña abierta sondea sola (lista y /estado cada 30 s, el chat cada pocos
# segundos). Con la base muda, cada sondeo esperaba los 10 s del `connect_timeout`
# del pool ocupando un hilo —el mismo ejecutor que usan pedidos y el sync— y
# dejaba un traceback completo en Railway, justo cuando hay que leer los logs.
#   · EL AVISO: una línea, como mucho una vez por minuto (`anotar_caida`).
#   · LA PAUSA: después de un fallo de conexión, las LECTURAS que la pantalla
#     repite sola contestan «kubera no contesta» de inmediato durante
#     `_PAUSA_CAIDA_S`. Las ESCRITURAS no miran la pausa: siempre lo intentan.
_PAUSA_CAIDA_S = 30.0          # un ciclo de sondeo de la pantalla
_AVISO_CAIDA_S = 60.0
_caida: dict[str, float | None] = {"hasta": 0.0, "aviso": None}
_caida_candado = threading.Lock()
_PG_SIN_CONEXION = ("57P01", "57P02", "57P03", "53300")


def es_caida(exc: BaseException) -> bool:
    """¿Falló la CONEXIÓN con kubera (y no la sentencia)? Sin código de Postgres
    no hubo servidor que contestara; la clase 08 es «excepción de conexión»."""
    if not isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError)):
        return False
    # Hijas de OperationalError que SÍ son de la sentencia (interbloqueo,
    # serialización, lock_timeout, statement_timeout): la base contestó.
    if isinstance(exc, (psycopg2.extensions.TransactionRollbackError,
                        psycopg2.extensions.QueryCanceledError)):
        return False
    codigo = _pgcode(exc)
    return codigo is None or str(codigo).startswith("08") or codigo in _PG_SIN_CONEXION


def anotar_caida(exc: BaseException, donde: str = "") -> None:
    """Arranca la pausa y deja UNA línea en el log (una por minuto, no una por
    petición). Sólo la primera línea del error: el resto es ruido de libpq."""
    ahora = time.monotonic()
    with _caida_candado:
        _caida["hasta"] = ahora + _PAUSA_CAIDA_S
        ultimo = _caida["aviso"]
        avisar = ultimo is None or ahora - ultimo >= _AVISO_CAIDA_S
        if avisar:
            _caida["aviso"] = ahora
    if avisar:
        primera = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
        log.warning("órdenes de venta: kubera no contesta%s (%s: %s). Las lecturas de la "
                    "pantalla contestan sin intentarlo durante %d s.",
                    f" [{donde}]" if donde else "", type(exc).__name__, primera[:200],
                    int(_PAUSA_CAIDA_S))


def en_pausa() -> bool:
    """¿Sigue viva la pausa de la última caída?"""
    return time.monotonic() < float(_caida["hasta"] or 0.0)


def _si_caida(cur: Any = None) -> None:
    """Para las lecturas que la pantalla repite sola: en pausa, ni se intenta."""
    if cur is None and en_pausa():
        raise SinBase()


def _reiniciar_caida() -> None:
    """Olvida la pausa y el aviso. Sólo para las pruebas."""
    with _caida_candado:
        _caida.update(hasta=0.0, aviso=None)


_SP = "ov_paso"


def _prestado(fn: Callable[[Any], Any], cur: Any) -> Any:
    """`fn` sobre el cursor que prestó una prueba, dentro de un SAVEPOINT: si la
    base rechaza el paso, la transacción de quien prestó el cursor sigue viva
    (y este módulo puede releer para explicar el rechazo). No confirma nada."""
    cur.execute(f"savepoint {_SP}")
    try:
        r = fn(cur)
    except BaseException:
        try:
            cur.execute(f"rollback to savepoint {_SP}")
            cur.execute(f"release savepoint {_SP}")
        except Exception:  # noqa: BLE001 — la conexión murió: manda el error original
            pass
        raise
    cur.execute(f"release savepoint {_SP}")
    return r


def _tx(fn: Callable[[Any], Any], cur: Any = None) -> Any:
    """Corre `fn(cursor)` en UNA unidad de trabajo.

    · Sin `cur`: una transacción corta del pool, con el reintento transitorio de
      la casa (conexión muerta, candado heredado). El COMMIT va al cerrar el
      `with`: ahí salen los errores DIFERIDOS, y por eso se atrapan aquí afuera.
    · Con `cur`: ese cursor, tal cual, sin confirmar (ver `_prestado`).

    Si lo que falla es la CONEXIÓN sale `SinBase` (502 con mensaje fijo y una
    línea de log por minuto). Lo demás sube como llegó: quien llama lo clasifica."""
    if cur is not None:
        try:
            return _prestado(fn, cur)
        except ErrorOV:
            raise
        except Exception as exc:  # noqa: BLE001 — se clasifica y se vuelve a lanzar
            if _sin_tabla(exc):
                raise FaltaMigracion() from exc
            raise

    def _hacer() -> Any:
        with sdb.get_cursor() as c:
            return fn(c)

    try:
        r = sdb.reintentar_transitorio(_hacer)
    except ErrorOV:
        raise
    except Exception as exc:  # noqa: BLE001 — se clasifica y se vuelve a lanzar
        if _sin_tabla(exc):
            raise FaltaMigracion() from exc
        if es_caida(exc):
            anotar_caida(exc)
            raise SinBase() from exc
        raise
    if _caida["hasta"]:
        _caida["hasta"] = 0.0          # kubera contestó: la pausa ya no tiene razón
    return r


def _dicts(cur: Any) -> list[dict[str, Any]]:
    """Las filas como dict, sea cual sea el cursor (el del pool es RealDictCursor;
    el que presta una prueba puede ser el de tuplas)."""
    if not cur.description:
        return []
    columnas = [d[0] for d in cur.description]
    return [dict(f) if isinstance(f, dict) else dict(zip(columnas, f)) for f in cur.fetchall()]


def _filas(sql: str, params: Any = None, cur: Any = None) -> list[dict[str, Any]]:
    def _f(c: Any) -> list[dict[str, Any]]:
        c.execute(sql, params)
        return _dicts(c)
    return _tx(_f, cur)


def _fila(sql: str, params: Any = None, cur: Any = None) -> dict[str, Any] | None:
    filas = _filas(sql, params, cur)
    return filas[0] if filas else None


def _transicion(operacion: str, sql: str, params: dict[str, Any],
                cur: Any = None) -> dict[str, Any] | None:
    """UNA transición = UN `execute` (más su COMMIT). Devuelve la fila del SELECT
    final, o lanza `_Rechazo` con el error YA clasificado.

    Lo único que se reintenta —UNA vez, y es seguro porque la sentencia es
    atómica y va guardada por su CAS— es lo que la guía manda reintentar:
    «ocupado» (venció el candado o el tiempo) y el interbloqueo, que además
    avisa porque significa que alguna sentencia rompió el orden de candados."""
    for intento in (1, 2):
        try:
            return _fila(sql, params, cur)
        except ErrorOV:
            raise
        except Exception as exc:  # noqa: BLE001 — sólo se tratan los errores de Postgres
            if _pgcode(exc) is None and not isinstance(exc, psycopg2.Error):
                raise
            clase, detalle = clasificar(exc, operacion)
            if clase == "deadlock":
                log.error("ordenes_venta.%s: INTERBLOQUEO (40P01), intento %d de 2. Alguna "
                          "sentencia rompió el orden de candados (guía §4.3).", operacion, intento)
            if clase in ("ocupado", "deadlock") and intento == 1:
                continue
            if clase == "ocupado":
                log.warning("ordenes_venta.%s: la bodega siguió ocupada tras el reintento "
                            "(pgcode=%s).", operacion, _pgcode(exc))
                raise Conflicto(_MSG_OCUPADO) from exc
            if clase == "deadlock":
                raise ErrorOV(_MSG_INESPERADO, status=502) from exc
            raise _Rechazo(clase, detalle, exc) from exc
    return None        # inalcanzable: el segundo intento devuelve o lanza


# ══════════════════════════════════════════════════════════════════════════════
# Lo que se pregunta UNA vez y se recuerda un rato: tablas, banderas y bucket
# ══════════════════════════════════════════════════════════════════════════════

_TTL_TABLAS = 60.0
_TTL_BANDERA = 30.0        # un apagado tarda a lo más medio minuto en surtir
_TTL_BUCKET = 60.0
_TTL_FALLO = 5.0           # una lectura FALLIDA no se recuerda medio minuto
_cache: dict[str, tuple[float, Any]] = {}
_cache_candado = threading.Lock()


def _recordado(llave: str) -> tuple[bool, Any]:
    with _cache_candado:
        visto = _cache.get(llave)
    if visto and time.monotonic() < visto[0]:
        return True, visto[1]
    return False, None


def _recordar(llave: str, valor: Any, ttl: float) -> None:
    with _cache_candado:
        _cache[llave] = (time.monotonic() + ttl, valor)


def _olvidar_cache() -> None:
    """Olvida tablas, banderas y bucket. Para las pruebas (y tras un acta, si urge)."""
    with _cache_candado:
        _cache.clear()


# Guía §4.8: mientras la 0064/0065 no estén en producción, quien las lee
# pregunta primero. Una sola consulta, sin tocar las tablas.
_SQL_HAY_TABLAS = ("select to_regclass('ventas.ov_ordenes') is not null "
                   "and to_regclass('almacen.almacenes') is not null "
                   "and to_regclass('almacen.stock_almacen') is not null as listas")


def _leer_tablas(cur: Any = None) -> bool:
    """La pregunta a la base, sin caché. (Es lo que una prueba sustituye para
    simular una base sin las migraciones.)"""
    fila = _fila(_SQL_HAY_TABLAS, None, cur)
    return bool(fila and fila.get("listas"))


def _tablas(refrescar: bool = False, cur: Any = None) -> bool | None:
    """True / False, o None si no se pudo PREGUNTAR (que no es lo mismo que «faltan»)."""
    if cur is None and not refrescar:
        hay, valor = _recordado("tablas")
        if hay:
            return valor
    try:
        valor = _leer_tablas(cur)
    except Exception as exc:  # noqa: BLE001 — «nunca truena»: no saber no es un error aquí
        if not isinstance(exc, SinBase):
            log.warning("ordenes_venta: no se pudo preguntar si están las tablas (%s)",
                        type(exc).__name__)
        return None
    if cur is None:
        _recordar("tablas", valor, _TTL_TABLAS)
    return valor


def tablas_listas(refrescar: bool = False, cur: Any = None) -> bool:
    """¿Están `ventas.ov_ordenes`, `almacen.almacenes` y `almacen.stock_almacen`? Con caché de
    60 s. NUNCA truena: si no se pudo preguntar contesta False."""
    return _tablas(refrescar, cur) is True


def _exigir_tablas(cur: Any = None) -> None:
    t = _tablas(cur=cur)
    if t is False:
        raise FaltaMigracion()
    if t is None:
        raise SinBase()


_BANDERA_APAGADA = {"encendido": False, "persistido": False, "actualizado_por": None,
                    "motivo": None, "actualizado_at": None}
# La variable de respaldo de cada bandera (guía §4.8). La que no está aquí no
# tiene: sin fila, apagada.
_RESPALDO = {BANDERA_MODULO: "ordenes_venta_enabled"}


def _leer_bandera(nombre: str, cur: Any = None) -> dict[str, Any] | None:
    """La fila de la bandera, o None si nadie la ha creado. Sin caché."""
    return _fila("select valor, motivo, actualizado_por, actualizado_at "
                 "from ops.automatizacion_flags where flag = %(f)s", {"f": nombre}, cur)


def estado_bandera(nombre: str, refrescar: bool = False, cur: Any = None) -> dict[str, Any]:
    """Una `Bandera` de tipos.ts. Sólo LECTURA: las banderas las enciende un acta
    (un UPDATE con motivo y quién), no la pantalla ni este módulo.

      · Hay fila → manda la fila.
      · No hay fila → manda la variable de respaldo, que vale `false`.
      · No se pudo LEER (tabla ausente, kubera caída, error) → APAGADA, diga lo
        que diga la variable (revisión SEG-05: en lo que aparta o vende, la duda
        cierra). Ese fallo se recuerda pocos segundos, no medio minuto.
    Nunca lanza."""
    llave = f"bandera:{nombre}"
    if cur is None and not refrescar:
        hay, valor = _recordado(llave)
        if hay:
            return dict(valor)
    if cur is None and en_pausa():
        return dict(_BANDERA_APAGADA)
    try:
        fila = _leer_bandera(nombre, cur)
    except Exception as exc:  # noqa: BLE001 — lectura fallida = apagada
        if not isinstance(exc, SinBase):
            log.warning("ordenes_venta: no se pudo leer la bandera %s (%s); se toma APAGADA",
                        nombre, type(exc).__name__)
        if cur is None:
            _recordar(llave, dict(_BANDERA_APAGADA), _TTL_FALLO)
        return dict(_BANDERA_APAGADA)
    if fila:
        b = {"encendido": bool(fila.get("valor")), "persistido": True,
             "actualizado_por": fila.get("actualizado_por"), "motivo": fila.get("motivo"),
             "actualizado_at": _plano(fila.get("actualizado_at"))}
    else:
        respaldo = _RESPALDO.get(nombre)
        b = {**_BANDERA_APAGADA,
             "encendido": bool(getattr(settings, respaldo, False)) if respaldo else False}
    if cur is None:
        _recordar(llave, dict(b), _TTL_BANDERA)
    return b


def habilitado(refrescar: bool = False, cur: Any = None) -> bool:
    """La bandera `ordenes_venta`. Apagada = modo prueba: sólo borradores.
    ⚠️ BLOQUEA cuando el caché vence: llamar desde un hilo (regla 11)."""
    return bool(estado_bandera(BANDERA_MODULO, refrescar, cur)["encendido"])


def generacion_auto(refrescar: bool = False, cur: Any = None) -> bool:
    """La bandera `ov_generacion_auto` (fase B): la lee el planeador antes de
    asignar a TEX3, y `crear_auto` la vuelve a exigir."""
    return bool(estado_bandera(BANDERA_AUTO, refrescar, cur)["encendido"])


def _leer_bucket(cur: Any = None) -> bool:
    fila = _fila("select exists (select 1 from storage.buckets where id = %(b)s) as hay",
                 {"b": BUCKET}, cur)
    return bool(fila and fila.get("hay"))


def hay_bucket(refrescar: bool = False, cur: Any = None) -> bool:
    """¿Existe el bucket privado `ordenes-venta`? La 0064 NO lo crea (va aparte,
    con su retención decidida) y mientras no exista `ventas.ov_archivos` no tiene
    escritor. Con caché de 60 s; si no se pudo preguntar, False. Nunca lanza."""
    if cur is None and not refrescar:
        hay, valor = _recordado("bucket")
        if hay:
            return bool(valor)
    if cur is None and en_pausa():
        return False
    try:
        valor = _leer_bucket(cur)
    except Exception as exc:  # noqa: BLE001 — sin saber, no hay dónde guardar
        if not isinstance(exc, (SinBase, FaltaMigracion)):
            log.warning("ordenes_venta: no se pudo preguntar por el bucket (%s)",
                        type(exc).__name__)
        if cur is None:
            _recordar("bucket", False, _TTL_FALLO)
        return False
    if cur is None:
        _recordar("bucket", valor, _TTL_BUCKET)
    return valor


_SQL_BODEGAS = """
select codigo, nombre, fuente, admite_ov, surte_ventas, cuenta_para_woo
  from almacen.almacenes
 order by (fuente = 'kubera') desc, admite_ov desc, preferencia nulls last, codigo
"""


def bodegas(cur: Any = None) -> list[dict[str, Any]]:
    """El catálogo `almacen.almacenes` (tipo Bodega de tipos.ts), las de kubera que
    admiten OV primero. Este módulo NUNCA lo escribe: sus banderas cambian con acta."""
    return [{"codigo": f["codigo"], "nombre": f["nombre"], "fuente": f["fuente"],
             "admite_ov": bool(f["admite_ov"]), "surte_ventas": bool(f["surte_ventas"]),
             "cuenta_para_woo": bool(f["cuenta_para_woo"])}
            for f in _filas(_SQL_BODEGAS, None, cur)]


def _bodegas_mapa(cur: Any = None) -> dict[str, dict[str, Any]]:
    return {b["codigo"]: b for b in bodegas(cur)}


def _porque_no_bodega(codigo: str, mapa: dict[str, dict[str, Any]]) -> str | None:
    """Por qué un renglón NO puede nombrar esa bodega, o None si puede. La FK de
    la base sólo frena las de Odoo: TEX3 apagada o REVISION pasarían en un
    borrador y tronarían al confirmar, así que se dice desde la captura (guía §4.6)."""
    b = mapa.get(codigo)
    if not b:
        return f"no existe la bodega «{codigo}»"
    if b["fuente"] != "kubera":
        return (f"{codigo} es una bodega de Odoo; las órdenes de venta sólo salen de "
                "bodegas de kubera")
    if not b["admite_ov"]:
        return f"la bodega {codigo} no admite órdenes de venta (está apagada)"
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Validación (pura: no toca la base)
# ══════════════════════════════════════════════════════════════════════════════

# Lo que no se puede guardar: el NUL (ni psycopg2 ni Postgres lo aceptan) y los
# SUSTITUTOS SUELTOS (U+D800–U+DFFF). Un sustituto suelto no necesita mala fe:
# basta un emoji cortado a la mitad por un `slice` del cliente. No se puede
# codificar a UTF-8, así que psycopg2 tronaba con UnicodeEncodeError al mandar el
# parámetro y a la persona le salía un 502 sin decirle qué campo (y una traza
# entera en el log por cada intento). Media pareja no es un carácter: se quita.
_NO_GUARDABLE = re.compile("[\x00\ud800-\udfff]")


def _limpio(v: Any) -> str:
    """Texto recortado, sin NUL ni sustitutos sueltos (ver `_NO_GUARDABLE`)."""
    if v is None:
        return ""
    return _NO_GUARDABLE.sub("", str(v)).strip()


def _texto(v: Any, tope: int, campo: str) -> str | None:
    """Recorta; '' → None; más largo que el tope → Invalido (no se trunca a escondidas)."""
    t = _limpio(v)
    if not t:
        return None
    if len(t) > tope:
        raise Invalido(f"«{_ETIQUETA.get(campo, campo)}» admite hasta {tope} caracteres "
                       f"(lleva {len(t)}).")
    return t


def _dinero(v: Any, campo: str, maximo: Decimal = MAX_IMPORTE) -> Decimal:
    """Un importe a 2 decimales entre 0 y `maximo`. Los bool no son números aquí."""
    if isinstance(v, bool) or v is None or (isinstance(v, str) and not v.strip()):
        raise Invalido(f"«{campo}» debe ser un número.")
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        raise Invalido(f"«{campo}» debe ser un número.") from None
    if not d.is_finite():
        raise Invalido(f"«{campo}» debe ser un número.")
    # El rango ANTES de redondear: un 1e30 no cabe en el quantize y tronaría.
    if d < 0 or d > maximo:
        raise Invalido(f"«{campo}» debe estar entre 0 y {maximo:,.2f}.")
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _entero_estricto(v: Any) -> int | None:
    """Un entero de verdad: `2`, `2.0` y "2" pasan; `2.5`, `True` y "dos" no."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if math.isfinite(v) and v == int(v) else None
    if isinstance(v, str) and re.fullmatch(r"\d{1,7}", v.strip()):
        return int(v.strip())
    return None


def _cantidad(v: Any, donde: str) -> int:
    """Piezas: entero de 1 a 100,000."""
    n = _entero_estricto(v)
    if n is None or n < 1 or n > MAX_CANTIDAD:
        raise Invalido(f"{donde}: la cantidad debe ser un entero entre 1 y {MAX_CANTIDAD:,}.")
    return n


def _imagen(v: Any) -> str | None:
    """Sólo una URL http(s). Un data-URI o una ruta local no se guardan."""
    t = _limpio(v)
    if not t or len(t) > 1000 or not t.lower().startswith(("http://", "https://")):
        return None
    return t


def _fecha(v: Any, campo: str) -> datetime | None:
    """ISO 8601 → timestamptz. Sin zona se entiende hora de CDMX (así captura la gente)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, datetime):
        d = v
    elif isinstance(v, date):
        d = datetime(v.year, v.month, v.day)
    else:
        t = str(v).strip()
        if t[-1:] in ("Z", "z"):
            t = t[:-1] + "+00:00"
        try:
            d = datetime.fromisoformat(t)
        except ValueError:
            raise Invalido(f"«{_ETIQUETA.get(campo, campo)}» no es una fecha válida "
                           f"(se espera ISO 8601, p. ej. 2026-10-02T15:30:00-06:00).") from None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_ZONA)
    if not 2000 <= d.year <= 2100:
        raise Invalido(f"«{_ETIQUETA.get(campo, campo)}» está fuera de rango.")
    return d


def _rev(v: Any) -> int:
    if isinstance(v, bool) or v is None:
        raise Invalido("Falta la «rev» de la orden.")
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise Invalido("Falta la «rev» de la orden.") from None
    if n < 1:
        raise Invalido("Falta la «rev» de la orden.")
    return n


def _motivo_texto(v: Any, minimo: int = 0, falta: str | None = None) -> str | None:
    """Un motivo escrito por una persona: recortado, con su mínimo (el de la base,
    dicho antes y con palabras) y su tope."""
    t = _limpio(v)
    if len(t) < minimo:
        raise Invalido(falta or f"Escribe el motivo ({minimo} caracteres o más).")
    if len(t) > MAX_MOTIVO:
        raise Invalido(f"El motivo admite hasta {MAX_MOTIVO} caracteres.")
    return t or None


_TOTAL_AUTO = object()     # `total: null` = que sea la suma de los renglones


def _encabezado(datos: dict[str, Any], parcial: bool) -> dict[str, Any]:
    """El encabezado validado. `parcial` (guardar): sólo las llaves PRESENTES —lo
    que no se manda no se toca—. Completo (alta): todas, con sus valores por omisión."""
    v: dict[str, Any] = {}
    for campo, tope in _TEXTOS.items():
        if campo in datos or not parcial:
            t = _texto(datos.get(campo), tope, campo)
            if t and campo in _EN_MINUSCULAS:
                t = t.lower()
            elif t and campo in _EN_MAYUSCULAS:
                t = t.upper()
            v[campo] = t
    if v.get("canal") and v["canal"] not in CANALES:
        raise Invalido("El canal debe ser uno de: " + ", ".join(CANALES) + ".")
    for campo in _FECHAS:
        if campo in datos or not parcial:
            v[campo] = _fecha(datos.get(campo), campo)
    if "moneda" in datos or not parcial:
        m = _limpio(datos.get("moneda")).upper() or "MXN"
        if not re.fullmatch(r"[A-Z]{3}", m):
            raise Invalido("La moneda son tres letras (MXN, USD…).")
        v["moneda"] = m
    if "total" in datos or not parcial:
        t = datos.get("total")
        v["total"] = _TOTAL_AUTO if t is None else _dinero(t, "total")
    if "comision" in datos or not parcial:
        c = datos.get("comision")
        v["comision"] = Decimal("0.00") if c is None else _dinero(c, "comisión")
    if "precio_origen" in datos or not parcial:
        p = _limpio(datos.get("precio_origen")).lower() or "manual"
        if p not in ("manual", "marketplace"):
            raise Invalido("«precio_origen» es 'manual' o 'marketplace'.")
        v["precio_origen"] = p
    return v


def _lineas(crudas: Any, mapa: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Los renglones validados, agrupados por (SKU, bodega) y numerados 1..n.

    · La BODEGA es opcional en un borrador. Si viene, tiene que ser una de
      kubera que admita OV (`mapa` es el catálogo; sin él no se revisa, que es
      como lo usan las pruebas puras).
    · El mismo SKU en la misma bodega dos veces se SUMA: `(orden_id, sku,
      almacen)` es único en la tabla (ov_lineas_sku_alm_uq, diferida: tronaría
      al COMMIT). El mismo SKU en DOS bodegas son dos renglones, y está bien.
    · …pero sólo se suma si traen el MISMO precio. Con precios distintos se
      sumaban las piezas al precio del primero y el dinero cambiaba sin avisar:
      no se adivina cuál quiso la persona, se le dice."""
    if crudas is None:
        return []
    if not isinstance(crudas, (list, tuple)):
        raise Invalido("«lineas» debe ser una lista de renglones.")
    if len(crudas) > MAX_RENGLONES * 10:
        raise Invalido(f"Una orden admite hasta {MAX_RENGLONES} renglones.")
    por_llave: dict[tuple[str, str | None], dict[str, Any]] = {}
    for i, c in enumerate(crudas, 1):
        donde = f"Renglón {i}"
        if not isinstance(c, dict):
            raise Invalido(f"{donde}: formato inválido.")
        sku = _limpio(c.get("sku"))
        if not 1 <= len(sku) <= 80:
            raise Invalido(f"{donde}: el SKU es obligatorio (1 a 80 caracteres).")
        cantidad = _cantidad(c.get("cantidad"), donde)
        precio = c.get("precio_unitario")
        precio = (Decimal("0.00") if precio is None
                  else _dinero(precio, f"{donde}: precio unitario", MAX_PRECIO))
        almacen = _limpio(c.get("almacen")).upper() or None
        if almacen and mapa is not None:
            porque = _porque_no_bodega(almacen, mapa)
            if porque:
                raise Invalido(f"{donde} ({sku}): {porque}.")
        llave = (sku.lower(), almacen)
        ya = por_llave.get(llave)
        if ya:
            if ya["precio_unitario"] != precio:
                raise Invalido(f"{donde}: el SKU {sku} viene dos veces"
                               + (f" en {almacen}" if almacen else "")
                               + f" con precios distintos ({ya['precio_unitario']} y {precio}). "
                               "Júntalo en un solo renglón con el precio correcto.")
            ya["cantidad"] += cantidad
            if ya["cantidad"] > MAX_CANTIDAD:
                raise Invalido(f"{donde}: {sku} suma más de {MAX_CANTIDAD:,} piezas.")
            continue
        por_llave[llave] = {"sku": sku, "titulo": (_limpio(c.get("titulo")) or None),
                            "imagen": _imagen(c.get("imagen")), "cantidad": cantidad,
                            "precio_unitario": precio, "almacen": almacen}
    if len(por_llave) > MAX_RENGLONES:
        raise Invalido(f"Una orden admite hasta {MAX_RENGLONES} renglones.")
    salida = []
    for n, r in enumerate(por_llave.values(), 1):
        if r["titulo"] and len(r["titulo"]) > 300:
            r["titulo"] = r["titulo"][:300]
        salida.append({"linea": n, **r})
    return salida


def _entrega(crudas: Any, lineas_orden: list[dict[str, Any]]) -> list[dict[str, int]]:
    """Lo que salió de cada renglón, validado contra los renglones de la orden:
    `[{id, n}]` con n de 0 a `cantidad`. Sin `crudas` salen TODOS los pendientes
    completos. Un renglón se entrega UNA vez: lo que no salió se suelta."""
    pendientes = {int(l["id"]): l for l in lineas_orden if l.get("entregado_at") is None}
    if crudas is None:
        return [{"id": i, "n": int(l["cantidad"])} for i, l in pendientes.items()]
    if not isinstance(crudas, (list, tuple)) or not crudas:
        raise Invalido("Indica qué renglones salieron y cuántas piezas de cada uno.")
    de_la_orden = {int(l["id"]): l for l in lineas_orden}
    salida: list[dict[str, int]] = []
    vistos: set[int] = set()
    for c in crudas:
        if not isinstance(c, dict):
            raise Invalido("Cada renglón entregado lleva su «id» y sus piezas «n».")
        lid = _entero_estricto(c.get("id"))
        n = _entero_estricto(c.get("n"))
        if lid is None or lid not in de_la_orden:
            raise Invalido("Uno de los renglones indicados no es de esta orden.")
        l = de_la_orden[lid]
        if lid in vistos:
            raise Invalido(f"El renglón de {l['sku']} viene dos veces.")
        vistos.add(lid)
        if lid not in pendientes:
            raise Invalido(f"El renglón de {l['sku']} ya había salido.")
        if n is None or n < 0 or n > int(l["cantidad"]):
            raise Invalido(f"{l['sku']}: las piezas que salieron van de 0 a {int(l['cantidad'])}.")
        salida.append({"id": lid, "n": n})
    return salida


def _suma(lineas: list[dict[str, Any]]) -> Decimal:
    return _total_que_cabe(
        sum((_d(r["precio_unitario"]) * int(r["cantidad"]) for r in lineas), Decimal("0.00")))


def _total_que_cabe(total: Decimal) -> Decimal:
    """El total AUTOMÁTICO (la suma de los renglones) también tiene tope. Cada
    renglón pasa su validación por separado, pero la suma puede no caber en el
    numeric(14,2) de la tabla: sin esto la base contestaba 22003 y una captura
    absurda salía como «bug» (502 y log.error) en vez de un 400 con palabras."""
    if total > MAX_IMPORTE:
        raise Invalido(f"El total de los renglones pasa de {MAX_IMPORTE:,}; revisa "
                       "cantidades y precios.")
    return total


def _d(v: Any) -> Decimal:
    """Un número que ya vino de la base (Decimal, o float si salió por JSON) a 2 decimales."""
    return Decimal(str(v if v is not None else 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _plano(v: Any) -> Any:
    """Lo que el JSON de la API entiende: ni Decimal ni datetime sin convertir."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _json(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=_plano)


def _lineas_json(lineas: list[dict[str, Any]]) -> str:
    """Para `jsonb_to_recordset`: el precio viaja como texto para no pasar por float."""
    return json.dumps([{**r, "precio_unitario": str(r["precio_unitario"])} for r in lineas],
                      ensure_ascii=False)


def _escapar_like(q: str) -> str:
    """Los comodines de quien busca son letras: `100%` busca «100%», no «100 y lo que sea»."""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _nombre_pdf(nombre: Any) -> str:
    """El nombre con el que se guarda y se baja: sin rutas, sin caracteres de control."""
    t = re.sub(r"[\x00-\x1f\x7f]", "", str(nombre or "")).replace("\\", "/").rsplit("/", 1)[-1]
    t = re.sub(r"\s+", " ", t).strip().strip(".")[:150].strip()
    if not t:
        t = "documento"
    return t if t.lower().endswith(".pdf") else t + ".pdf"


def _peso(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, round(n / 1024))} KB"


def _piezas(n: int) -> str:
    return f"{n} pieza" if n == 1 else f"{n} piezas"


def _renglones(n: int) -> str:
    return f"{n} renglón" if n == 1 else f"{n} renglones"


def _quedan(n: int) -> str:
    return "queda 1 renglón por salir" if n == 1 else f"quedan {n} renglones por salir"


def _firma(quien: Quien) -> dict[str, Any]:
    """Los tres parámetros de autoría de toda escritura: `q`, `nombre` y `via`.

    Guía §4.4: `*_por`, `autor` y `quien` NUNCA van vacíos (son NOT NULL y un
    `None` es un 23502) y la `via` sólo acepta el catálogo de la 0064. Lo que no
    cabe NO se disfraza de «panel»: se rechaza, porque una bitácora que miente
    se consulta igual y se le cree."""
    via = quien.via if quien.via in VIAS else ("automatico" if quien.via == "cron" else None)
    if via is None:
        raise Invalido(f"No se reconoce por dónde entra el cambio («{quien.via}»): se espera "
                       "panel, api, claude o automatico.")
    actor = _limpio(quien.actor)
    if not actor:
        if via != "automatico":
            raise SinPermiso("No se pudo identificar quién hace el cambio.")
        actor = AUTOMATICO.actor
    return {"q": actor, "nombre": _limpio(quien.nombre) or None, "via": via}


def _op() -> str:
    """La marca de ESTA operación; va en `datos.op` del mensaje que escribe la sentencia."""
    return uuid.uuid4().hex


# ══════════════════════════════════════════════════════════════════════════════
# Permisos (puros: la pantalla pinta con ellos y cada operación los exige)
# ══════════════════════════════════════════════════════════════════════════════

ACCIONES = ("editar", "confirmar", "entregar", "cancelar", "borrar", "responder_salio",
            "salio_tarde", "mensajes", "subir_archivo", "bajar_archivo", "borrar_archivo")

# La CLASE del motivo decide el error: el rol es un 403, el estado un 400, la
# bandera apagada el 409 de «modo prueba» y la falta del bucket otro 409.
_ROL, _ESTADO, _APAGADO, _SIN_BUCKET = "rol", "estado", "apagado", "sin_bucket"

_SOLO_ADMIN = {"borrar": "Sólo un administrador puede borrar una orden",
               "borrar_archivo": "Sólo un administrador puede quitar un PDF",
               "salio_tarde": ("Sólo un administrador puede registrar la salida de una "
                               "orden ya cancelada")}


def _no_escribe(quien: Quien) -> tuple[str, str]:
    if quien.rol == "lectura":
        return _ROL, "Tu rol es de sólo lectura"
    return _ROL, "Tu usuario no tiene permiso para mover órdenes de venta"


def _motivo(accion: str, orden: dict[str, Any], quien: Quien, encendido: bool,
            bucket: bool = False) -> tuple[str, str] | None:
    """Por qué NO se puede `accion`, o None si se puede. Primero el rol (es lo que
    no cambia recargando), luego si está borrada, luego el estado de la orden y
    al final la bandera."""
    estado = orden.get("estado") or "borrador"
    borrada = bool(orden.get("borrada_at"))
    rotulo = _ROTULO.get(estado, estado)

    if accion == "bajar_archivo":
        # El RBAC por prefijo deja pasar el GET a `lectura` (no puede separar la
        # descarga: el id va en medio de la ruta), así que el permiso se exige
        # aquí: operador o admin, y de una orden BORRADA sólo admin.
        if not quien.escribe:
            return _no_escribe(quien)
        if borrada and not quien.admin:
            return _ROL, "Sólo un administrador puede bajar los PDF de una orden borrada"
        return None
    if accion in _SOLO_ADMIN and not quien.admin:
        return _ROL, _SOLO_ADMIN[accion]
    if not quien.escribe:
        return _no_escribe(quien)
    if borrada:
        return _ESTADO, ("La orden ya está borrada" if accion == "borrar"
                         else "La orden está borrada")

    if accion in ("borrar", "mensajes", "borrar_archivo"):
        return None
    if accion == "subir_archivo":
        return None if bucket else (_SIN_BUCKET, _MSG_SIN_BUCKET)
    if accion == "editar":
        if estado != "borrador":
            return _ESTADO, (f"La orden ya está {rotulo}: su contenido no cambia. Para "
                             "corregirla hay que cancelarla (o que un administrador la borre)")
        return None
    if accion == "confirmar":
        if estado != "borrador":
            return _ESTADO, "Sólo un borrador se puede confirmar"
        if not encendido:
            return _APAGADO, "Modo prueba: confirmar está apagado (bandera «ordenes_venta»)"
        if int(orden.get("renglones") or 0) < 1:
            return _ESTADO, "La orden no tiene renglones"
        return None
    if accion == "entregar":
        if estado != "confirmada":
            return _ESTADO, "Sólo una orden confirmada se puede entregar"
        if orden.get("canal_cancelo_at"):
            return _ESTADO, ("El canal canceló esta venta con el paquete en camino: "
                             "primero hay que contestar si salió")
        if not encendido:
            return _APAGADO, "Modo prueba: entregar está apagado (bandera «ordenes_venta»)"
        return None
    if accion == "cancelar":
        if estado == "borrador":
            return None
        if estado == "confirmada":
            if not quien.admin:
                return _ROL, "Sólo un administrador puede cancelar una orden confirmada"
            if orden.get("canal_cancelo_at"):
                # Con la pregunta abierta, cancelar a mano sería una tercera salida
                # que se la salta y queda como cancelación manual: se contesta.
                return _ESTADO, ("El canal canceló esta venta con el paquete en camino: "
                                 "primero hay que contestar si salió")
            return None
        if estado == "entregada":
            return _ESTADO, ("Una orden entregada no se cancela desde aquí: si el canal la "
                             "cancela, queda como entregada y cancelada")
        return _ESTADO, "La orden ya está cancelada"
    if accion == "responder_salio":
        if estado != "confirmada" or not orden.get("canal_cancelo_at"):
            return _ESTADO, "La orden no está esperando la respuesta de «¿salió?»"
        return None
    if accion == "salio_tarde":
        if estado != "cancelada":
            return _ESTADO, "Sólo una orden cancelada puede registrarse como salida tarde"
        if not orden.get("confirmada_at"):
            return _ESTADO, ("Esa orden se canceló siendo borrador: nunca estuvo confirmada "
                             "y no pudo haber salido")
        return None
    return _ESTADO, f"Acción desconocida: {accion}"


def permisos(orden: dict[str, Any], quien: Quien, encendido: bool,
             bucket: bool = False) -> dict[str, Any]:
    """Qué puede hacer `quien` con esta orden (tipo Permisos de tipos.ts). PURA.

    `encendido` es la bandera `ordenes_venta` y `bucket` si existe el bucket de
    los PDF: los dos se preguntan afuera (aquí no se toca la base). `porque`
    explica cada «no», para el `title` del botón apagado."""
    p: dict[str, Any] = {}
    porque: dict[str, str] = {}
    for accion in ACCIONES:
        m = _motivo(accion, orden, quien, encendido, bucket)
        p[accion] = m is None
        if m:
            porque[accion] = m[1]
    p["porque"] = porque
    return p


def _error_de(m: tuple[str, str]) -> ErrorOV:
    clase, texto = m
    if clase == _ROL:
        return SinPermiso(texto + ".")
    if clase == _APAGADO:
        return Apagado()
    if clase == _SIN_BUCKET:
        return Conflicto(texto)
    return Invalido(texto + ".")


# ══════════════════════════════════════════════════════════════════════════════
# Leer una orden (UNA consulta: encabezado, derivados, renglones y PDF)
# ══════════════════════════════════════════════════════════════════════════════

_COLS = """
       o.id, o.folio, o.estado, o.tipo, o.rev, o.cliente, o.canal, o.mp_canal, o.mp_cuenta,
       o.mp_orden, o.full_tienda, o.envio_ref, o.descripcion, o.guia, o.paqueteria,
       o.fecha_venta, o.entrega_limite, o.moneda, o.total, o.comision,
       o.total - o.comision as neto, o.precio_origen,
       o.devolucion_estado, o.canal_cancelo_at, o.canal_cancelo_ref,
       o.creado_at, o.creado_por, o.creado_nombre, o.creado_via,
       o.confirmada_at, o.confirmada_por, o.confirmada_nombre,
       o.entregada_at, o.entregada_por, o.entregada_nombre,
       o.cancelada_at, o.cancelada_por, o.cancelada_nombre, o.cancelada_origen, o.cancelada_motivo,
       o.borrada_at, o.borrada_por, o.borrada_nombre, o.borrada_motivo, o.actualizado_at,
       coalesce(r.renglones, 0) as renglones, coalesce(r.piezas, 0) as piezas,
       coalesce(r.piezas_apartadas, 0) as piezas_apartadas,
       coalesce(r.piezas_entregadas, 0) as piezas_entregadas,
       coalesce(r.renglones_entregados, 0) as renglones_entregados,
       coalesce(r.skus, '{}'::text[]) as skus, coalesce(r.bodegas, '{}'::text[]) as bodegas,
       (select count(*) from ventas.ov_archivos a
         where a.orden_id = o.id and a.borrado_at is null) as n_archivos,
       (select count(*) from ventas.ov_mensajes m where m.orden_id = o.id) as n_mensajes"""

# El saldo del SKU EN SU BODEGA: sin bodega o sin fila de saldo, los tres salen
# NULL («no lo sabemos»), que la pantalla pinta distinto de un cero.
_COLS_DETALLE = """,
       coalesce((select json_agg(json_build_object(
                    'id', l.id, 'linea', l.linea, 'sku', l.sku::text, 'titulo', l.titulo,
                    'imagen', l.imagen, 'cantidad', l.cantidad,
                    'precio_unitario', l.precio_unitario,
                    'importe', l.cantidad * l.precio_unitario, 'almacen', l.almacen,
                    'reservado', l.reservado, 'entregado', l.entregado,
                    'entregado_at', l.entregado_at, 'entregado_por', l.entregado_por,
                    'fisico', sa.fisico, 'apartado', sa.apartado, 'libre', sa.libre,
                    'conocido', p.sku is not null) order by l.linea, l.id)
                   from ventas.ov_lineas l
                   left join almacen.stock_almacen sa on sa.sku = l.sku and sa.almacen = l.almacen
                   left join core.products p on p.sku = l.sku
                  where l.orden_id = o.id), '[]'::json) as lineas,
       coalesce((select json_agg(json_build_object(
                    'id', a.id, 'orden_id', a.orden_id, 'tipo', a.tipo, 'nombre', a.nombre,
                    'bytes', a.bytes, 'sha256', a.sha256, 'subido_at', a.subido_at,
                    'subido_por', a.subido_por, 'subido_nombre', a.subido_nombre) order by a.id)
                   from ventas.ov_archivos a
                  where a.orden_id = o.id and a.borrado_at is null), '[]'::json) as archivos"""

_DESDE = """
  from ventas.ov_ordenes o
  left join lateral (
       select count(*) as renglones, sum(l.cantidad) as piezas,
              sum(l.reservado) as piezas_apartadas,
              coalesce(sum(l.entregado), 0) as piezas_entregadas,
              count(*) filter (where l.entregado_at is not null) as renglones_entregados,
              (array_agg(l.sku::text order by l.linea, l.id))[1:12] as skus,
              array_agg(distinct l.almacen) filter (where l.almacen is not null) as bodegas
         from ventas.ov_lineas l where l.orden_id = o.id) r on true"""

_SQL_DETALLE = "select" + _COLS + _COLS_DETALLE + _DESDE


def _tres_skus(skus: Any) -> list[str]:
    """Hasta tres SKUs DISTINTOS, en el orden de los renglones (el mismo SKU puede
    venir en dos renglones si sale de dos bodegas)."""
    vistos: list[str] = []
    for s in skus or []:
        if s and s.lower() not in (v.lower() for v in vistos):
            vistos.append(s)
        if len(vistos) == 3:
            break
    return vistos


def _resumen(fila: dict[str, Any]) -> dict[str, Any]:
    """OrdenResumen de tipos.ts: las columnas tal cual más los derivados."""
    o = {k: _plano(v) for k, v in fila.items() if k not in ("lineas", "archivos")}
    for k in ("renglones", "piezas", "piezas_apartadas", "piezas_entregadas",
              "renglones_entregados", "n_archivos", "n_mensajes"):
        o[k] = int(o.get(k) or 0)
    o["skus"] = _tres_skus(fila.get("skus"))
    o["bodegas"] = [b for b in (fila.get("bodegas") or []) if b]
    return o


def _orden(fila: dict[str, Any], quien: Quien, cur: Any = None) -> dict[str, Any]:
    o = _resumen(fila)
    o["lineas"] = list(fila.get("lineas") or [])
    o["archivos"] = list(fila.get("archivos") or [])
    o["permisos"] = permisos(fila, quien, habilitado(cur=cur), hay_bucket(cur=cur))
    return o


def _id(orden_id: Any) -> int:
    try:
        if isinstance(orden_id, bool):
            raise ValueError
        return int(orden_id)
    except (TypeError, ValueError):
        raise NoExiste(f"No existe la orden de venta {orden_id}.") from None


def _leer_id(orden_id: Any, cur: Any = None) -> dict[str, Any]:
    n = _id(orden_id)
    fila = _fila(_SQL_DETALLE + " where o.id = %(id)s", {"id": n}, cur)
    if not fila:
        raise NoExiste(f"No existe la orden de venta {orden_id}.")
    return fila


def _leer_cabeza(orden_id: Any, cur: Any = None) -> dict[str, Any]:
    """Sólo lo que hace falta para decidir un permiso (el chat no paga el detalle)."""
    n = _id(orden_id)
    fila = _fila("select id, folio, estado, rev, borrada_at, confirmada_at, canal_cancelo_at "
                 "from ventas.ov_ordenes where id = %(id)s", {"id": n}, cur)
    if not fila:
        raise NoExiste(f"No existe la orden de venta {orden_id}.")
    return fila


def obtener(ref: int | str, quien: Quien, cur: Any = None) -> dict[str, Any]:
    """El detalle. `ref` = id o folio («OV-00012», sin distinguir mayúsculas)."""
    _exigir_tablas(cur)
    t = str(ref).strip()
    if isinstance(ref, int) and not isinstance(ref, bool) or re.fullmatch(r"\d{1,18}", t):
        return _orden(_leer_id(t, cur), quien, cur)
    if re.fullmatch(r"(?i)ov-\d{1,12}", t):
        fila = _fila(_SQL_DETALLE + " where o.folio = %(folio)s", {"folio": t.upper()}, cur)
        if fila:
            return _orden(fila, quien, cur)
    raise NoExiste(f"No existe la orden de venta {t or ref}.")


def _resp(orden_id: int, quien: Quien, mensaje: str, cur: Any = None) -> dict[str, Any]:
    """Toda escritura RELEE y contesta el estado real, no el que se pidió (RespOrden)."""
    return {"ok": True, "orden": _orden(_leer_id(orden_id, cur), quien, cur), "mensaje": mensaje}


def _preparar(orden_id: Any, rev: Any, quien: Quien, accion: str,
              cur: Any = None) -> dict[str, Any]:
    """Lee la orden y exige el permiso ANTES de escribir, para contestar con el
    porqué. La guarda de verdad sigue siendo el CAS de la sentencia (`rev` y
    estado): esto sólo evita un viaje y da mejor mensaje. El orden importa: el
    rol primero (403 aunque la rev sea vieja), la rev después (409: recarga) y
    el estado al final (400)."""
    _exigir_tablas(cur)
    o = _leer_id(orden_id, cur)
    m = _motivo(accion, o, quien, habilitado(cur=cur))
    if m and m[0] == _ROL:
        raise _error_de(m)
    if _rev(rev) != o["rev"]:
        raise Conflicto()
    if m:
        raise _error_de(m)
    return o


def _lo_hice_yo(orden_id: int, op: str, cur: Any = None) -> bool:
    """¿Mi marca ya está en la bitácora de esa orden? El CAS falló, pero si la
    transición es la MÍA (el COMMIT entró y la conexión murió al contestar; el
    reintento transitorio volvió a correr el cuerpo), no es un conflicto: es el
    éxito que fue. Guía §4.2: «antes de mostrarlo, relee»."""
    try:
        return bool(_fila("select 1 as mio from ventas.ov_mensajes "
                          "where orden_id = %(id)s and datos->>'op' = %(op)s limit 1",
                          {"id": orden_id, "op": op}, cur))
    except ErrorOV:
        return False


def _fallo(r: _Rechazo, operacion: str, orden_id: int, op: str, quien: Quien, hecho: str,
           cur: Any = None) -> dict[str, Any]:
    """El final común de una transición rechazada: si el rechazo es de negocio y
    la transición resulta ser la mía, contesta el éxito; si no, lanza el error
    que le toca a quien pide."""
    if r.clase == "negocio" and _lo_hice_yo(orden_id, op, cur):
        return _resp(orden_id, quien, hecho, cur)
    raise _error_de_rechazo(r, operacion) from r.exc


# ══════════════════════════════════════════════════════════════════════════════
# La liga con una venta de marketplace (mp_canal, mp_cuenta, mp_orden)
# ══════════════════════════════════════════════════════════════════════════════

def _por_clave(clave: str, cur: Any = None) -> dict[str, Any] | None:
    """La orden VIVA de esa clave de idempotencia (el índice único es parcial:
    una cancelada o borrada ya soltó su clave)."""
    return _fila(_SQL_DETALLE + " where o.clave = %(clave)s and o.borrada_at is null "
                 "and o.estado <> 'cancelada' order by o.id limit 1", {"clave": clave}, cur)


def _venta_ligada(mp_canal: str | None, mp_cuenta: str | None, mp_orden: str | None,
                  excepto: int | None = None, cur: Any = None) -> dict[str, Any] | None:
    """La orden VIVA (ni borrada ni cancelada) que ya tiene esa venta: la misma
    regla del índice único `ov_ordenes_mp_uq`."""
    if not (mp_canal and mp_cuenta and mp_orden):
        return None
    return _fila(
        """select id, folio, estado from ventas.ov_ordenes
            where mp_canal = %(canal)s and mp_cuenta = %(cuenta)s and mp_orden = %(orden)s
              and borrada_at is null and estado <> 'cancelada' and id <> %(excepto)s
            order by id limit 1""",
        {"canal": mp_canal, "cuenta": mp_cuenta, "orden": mp_orden, "excepto": excepto or 0}, cur)


def _venta_en_canal(mp_canal: str, mp_orden: str, cur: Any = None) -> list[dict[str, Any]]:
    """Esa venta en channel.orders: una fila por CUENTA en la que exista, y si
    alguno de sus renglones es FULL. SÓLO LEE. Si el ambiente no tiene channel.*
    no hay contra qué comparar: lista vacía."""
    try:
        return _filas(
            """select c.cuenta,
                      exists (select 1 from channel.order_items i
                               where i.canal = c.canal and i.cuenta = c.cuenta
                                 and i.external_order_id = c.external_order_id
                                 and i.es_fulfillment) as es_full
                 from channel.orders c
                where c.canal = %(canal)s and c.external_order_id = %(orden)s
                order by c.cuenta
                limit 10""", {"canal": mp_canal, "orden": mp_orden}, cur)
    except FaltaMigracion:
        return []


def _ligar_venta(mp_canal: str | None, mp_cuenta: str | None, mp_orden: str | None,
                 cur: Any = None) -> tuple[str | None, str | None, str | None]:
    """Lo que se revisa al LIGAR una orden con una venta de marketplace. Devuelve
    el trío como queda (o tres None).

    · TODO O NADA (ov_ordenes_mp_chk): con una parte, hacen falta las tres.
    · LA CUENTA SE COMPLETA SOLA cuando no se dijo y channel.orders tiene esa
      venta en EXACTAMENTE una cuenta. Con el id en dos cuentas (ML) no se
      adivina: se pide.
    · UNA VENTA FULL NO LLEVA ORDEN PROPIA: sale del almacén del marketplace, y
      apartarle stock de bodega es reservar piezas que nadie va a surtir. FULL =
      algún renglón `es_fulfillment` (el dato fiable está en los renglones).

    Si la venta no está en channel.orders se permite: puede no haberse ingerido
    todavía (la base tampoco lo comprueba: decisión 14 de la 0064)."""
    if not (mp_canal or mp_cuenta or mp_orden):
        return None, None, None
    if not (mp_canal and mp_orden):
        raise Invalido("Para ligar una venta de marketplace hacen falta su canal, su cuenta y "
                       "su número de orden (los tres o ninguno).")
    filas = _venta_en_canal(mp_canal, mp_orden, cur)
    if not mp_cuenta and len(filas) == 1:
        mp_cuenta = _limpio(filas[0]["cuenta"]).upper()[:_TEXTOS["mp_cuenta"]] or None
    if not mp_cuenta:
        raise Invalido("Falta la cuenta de la venta de marketplace"
                       + (": esa orden existe en más de una cuenta, indica cuál."
                          if len(filas) > 1 else " (los tres datos o ninguno)."))
    suyas = [f for f in filas if _limpio(f["cuenta"]).upper() == mp_cuenta]
    if any(f["es_full"] for f in suyas):
        raise Invalido(_MSG_FULL)
    return mp_canal, mp_cuenta, mp_orden


def _choque_venta(mp_canal: str | None, mp_cuenta: str | None, mp_orden: str | None,
                  excepto: int | None = None, cur: Any = None) -> Conflicto:
    ya = None
    try:
        ya = _venta_ligada(mp_canal, mp_cuenta, mp_orden, excepto, cur)
    except Exception:  # noqa: BLE001 — el mensaje sale igual, sin el folio
        pass
    return Conflicto(f"Esa venta ya tiene la orden {ya['folio']}." if ya
                     else "Esa venta ya tiene una orden de venta.")


# ══════════════════════════════════════════════════════════════════════════════
# Alta de borrador (patrón b de la guía)
# ══════════════════════════════════════════════════════════════════════════════

# `greatest(5, length(…))`: lpad RECORTA cuando el número no cabe, y el folio
# 100000 saldría «OV-10000», que ya existe. El CHECK admite 5 dígitos o más.
# El UPDATE del contador bloquea su fila hasta el commit: todas las altas hacen
# fila ahí, así que esta sentencia es corta a propósito (no toca saldo). Si el
# alta falla, la sentencia entera se deshace y el folio NO se consume.
SQL_CREAR = _una("""
with f as (
  update ventas.ov_folio set ultimo = ultimo + 1 where id = 1 returning ultimo
), o as (
  insert into ventas.ov_ordenes (folio, estado, tipo, cliente, canal, mp_canal, mp_cuenta, mp_orden,
                              descripcion, guia, paqueteria, fecha_venta, entrega_limite, moneda,
                              total, comision, precio_origen, creado_por, creado_nombre,
                              creado_via, clave)
  select 'OV-' || lpad(f.ultimo::text, greatest(5, length(f.ultimo::text)), '0'), 'borrador',
         'venta', %(cliente)s, %(canal)s, %(mp_canal)s, %(mp_cuenta)s, %(mp_orden)s,
         %(descripcion)s, %(guia)s, %(paqueteria)s, %(fecha_venta)s, %(entrega_limite)s,
         %(moneda)s, %(total)s, %(comision)s, %(precio_origen)s, %(q)s, %(nombre)s, %(via)s,
         %(clave)s
    from f
  returning id, folio, rev
), l as (
  insert into ventas.ov_lineas (orden_id, linea, sku, titulo, imagen, cantidad, precio_unitario,
                             almacen)
  select o.id, x.linea, x.sku, x.titulo, x.imagen, x.cantidad, x.precio_unitario, x.almacen
    from o, jsonb_to_recordset(%(lineas)s::jsonb)
            as x(linea int, sku text, titulo text, imagen text, cantidad int,
                 precio_unitario numeric, almacen text)
  returning id
), m as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select o.id, 'sistema', 'creada', %(cuerpo)s, %(datos)s::jsonb, %(q)s, %(nombre)s, %(via)s
    from o
  returning id
)
select (select id from o) as id, (select folio from o) as folio, (select rev from o) as rev,
       ops.exigir((select count(*) from l) = jsonb_array_length(%(lineas)s::jsonb),
                  'renglones_no_cuadran')
     + ops.exigir((select count(*) from o) = 1 and (select count(*) from m) = 1,
                  'alta_escrituras_no_cuadran') as cuadra
""")


def _cuerpo_creada(lineas: list[dict[str, Any]], enc: dict[str, Any]) -> str:
    cuerpo = "Orden creada en borrador"
    if lineas:
        cuerpo += f" · {_renglones(len(lineas))}, {_piezas(sum(r['cantidad'] for r in lineas))}"
    else:
        cuerpo += " · sin renglones"
    if enc.get("mp_orden"):
        cuerpo += f" · venta {enc.get('mp_canal')} {enc['mp_orden']}"
    return cuerpo


def _lineas_bitacora(lineas: list[dict[str, Any]], reservado: bool = False) -> list[dict[str, Any]]:
    """`datos.lineas` de un mensaje del sistema: lo que el chat pinta por SKU."""
    return [{"sku": r["sku"], "titulo": r.get("titulo"), "cantidad": int(r["cantidad"]),
             "precio_unitario": float(_d(r.get("precio_unitario"))),
             "almacen": r.get("almacen"),
             "reservado": int(r["cantidad"]) if reservado else 0} for r in lineas]


def _ya_estaba(fila: dict[str, Any], quien: Quien, cur: Any = None) -> dict[str, Any]:
    return {"ok": True, "orden": _orden(fila, quien, cur),
            "mensaje": f"La orden {fila['folio']} ya estaba creada."}


def crear_borrador(datos: dict[str, Any], quien: Quien, clave: str | None = None,
                   cur: Any = None) -> dict[str, Any]:
    """Alta en BORRADOR (no depende de la bandera: un borrador no aparta nada).

    Idempotente por `clave` (el uuid que la pantalla genera una vez por
    formulario): el doble clic, dos peticiones a la vez y el reintento
    transitorio devuelven la orden que ya existe, no una segunda. La MISMA
    venta de marketplace capturada otra vez, con otra clave, es un 409 que dice
    el folio de la que ya la tiene."""
    if not quien.escribe:
        raise _error_de(_no_escribe(quien))
    if not isinstance(datos, dict):
        raise Invalido("El cuerpo de la orden no es válido.")
    tipo = _limpio(datos.get("tipo")).lower() or "venta"
    if tipo == "full":
        raise Invalido("Los envíos a FULL se crean desde «Crear FULL», no desde esta pantalla.")
    if tipo != "venta":
        raise Invalido("El tipo de la orden es «venta».")
    firma = _firma(quien)
    enc = _encabezado(datos, parcial=False)
    clave_cliente = _limpio(clave)
    if len(clave_cliente) > MAX_CLAVE:
        raise Invalido(f"La clave de idempotencia admite hasta {MAX_CLAVE} caracteres.")
    # «mp:…» y «full:…» son las claves DETERMINISTAS de crear_auto y de Crear
    # FULL (una por venta, una por envío). Si un cliente ocupara una, el proceso
    # automático recibiría «ya existía» y la venta real se quedaría con un
    # borrador escrito por una persona.
    if clave_cliente.lower().startswith(("mp:", "full:")):
        raise Invalido("La clave de idempotencia no puede empezar con «mp:» ni «full:»: "
                       "están reservadas a las órdenes automáticas.")
    # Sin clave del navegador (la API) se inventa una: es lo único que impide
    # que la repetición de un reintento transitorio cree otra orden.
    clave_final = clave_cliente or f"srv-{_op()}"

    _exigir_tablas(cur)
    lineas = _lineas(datos.get("lineas"), _bodegas_mapa(cur))
    if enc["total"] is _TOTAL_AUTO:
        enc["total"] = _suma(lineas)
    if clave_cliente:
        ya = _por_clave(clave_cliente, cur)
        if ya:
            return _ya_estaba(ya, quien, cur)
    enc["mp_canal"], enc["mp_cuenta"], enc["mp_orden"] = _ligar_venta(
        enc["mp_canal"], enc["mp_cuenta"], enc["mp_orden"], cur)
    ligada = _venta_ligada(enc["mp_canal"], enc["mp_cuenta"], enc["mp_orden"], cur=cur)
    if ligada:
        # La carrera de abajo, un paso antes: entre la búsqueda por clave y ésta,
        # la OTRA petición con MI clave pudo terminar. Si la venta la tiene mi
        # propia orden, es el éxito idempotente, no un 409 contra mí mismo.
        ya = _por_clave(clave_cliente, cur) if clave_cliente else None
        if ya and ya["id"] == ligada["id"]:
            return _ya_estaba(ya, quien, cur)
        raise Conflicto(f"Esa venta ya tiene la orden {ligada['folio']}.")

    bitacora: dict[str, Any] = {"op": _op(), "lineas": _lineas_bitacora(lineas),
                                "total": float(enc["total"])}
    if enc["mp_orden"]:
        bitacora["mp"] = {"canal": enc["mp_canal"], "cuenta": enc["mp_cuenta"],
                          "orden": enc["mp_orden"]}
    params = {**enc, **firma, "clave": clave_final, "lineas": _lineas_json(lineas),
              "cuerpo": _cuerpo_creada(lineas, enc), "datos": _json(bitacora)}
    try:
        fila = _transicion("crear_borrador", SQL_CREAR, params, cur)
    except _Rechazo as r:
        if r.clase != "ya_existia":
            raise _error_de_rechazo(r, "crear_borrador") from r.exc
        # 23505 de la clave o de la venta EN UN ALTA = ya existía: se relee.
        # PRIMERO por la clave, sea cual sea el índice que saltó: el reintento de
        # un alta LIGADA a una venta truena por el de la venta… contra SU PROPIA
        # orden, y leído como choque de venta sería un 409 contra uno mismo.
        ya = _por_clave(clave_final, cur)
        if ya:
            return _ya_estaba(ya, quien, cur)
        if r.detalle == "ov_ordenes_mp_uq":
            raise _choque_venta(enc["mp_canal"], enc["mp_cuenta"], enc["mp_orden"],
                                cur=cur) from r.exc
        raise Conflicto("Esa orden ya se había creado y se canceló o se borró mientras tanto; "
                        "vuelve a intentar.") from r.exc
    if not fila or not fila.get("id"):
        raise ErrorOV(_MSG_INESPERADO, status=502)
    return _resp(fila["id"], quien, f"Orden {fila['folio']} creada en borrador.", cur)


# ══════════════════════════════════════════════════════════════════════════════
# Guardar un borrador (encabezado y renglones, en UNA sentencia)
# ══════════════════════════════════════════════════════════════════════════════

# La guardia `o` es el UPDATE del encabezado (CAS de rev + estado): si no
# encuentra la fila, nada más escribe —`d`, `u`, `i` y `msg` cuelgan de ella— y
# `ops.exigir` deshace todo. Cada columna lleva su bandera `t_<campo>`: lo que
# la petición no mandó NO se toca, y aun así el texto de la sentencia es fijo.
#
# Los renglones: `d` borra los que ya no vienen, `u` actualiza los que siguen e
# `i` inserta los nuevos, casando por (sku, bodega). Tocan conjuntos de filas
# DISJUNTOS (cada fila se toca una vez por sentencia, guía §4.1 punto 4). Las
# dos llaves de renglón son DIFERIDAS: renumerar `linea` aquí no truena a media
# sentencia, y un duplicado saldría al COMMIT (no debe: Python ya agrupó).
# Empezar por el UPDATE de la orden no es casual: la guarda de `ov_lineas`
# bloquea esa misma fila, y dos «guardar» del mismo borrador hacen fila ahí.
SQL_GUARDAR = _una("""
with o as (
  update ventas.ov_ordenes v
     set cliente        = case when %(t_cliente)s        then %(cliente)s        else v.cliente end,
         canal          = case when %(t_canal)s          then %(canal)s          else v.canal end,
         mp_canal       = case when %(t_mp_canal)s       then %(mp_canal)s       else v.mp_canal end,
         mp_cuenta      = case when %(t_mp_cuenta)s      then %(mp_cuenta)s      else v.mp_cuenta end,
         mp_orden       = case when %(t_mp_orden)s       then %(mp_orden)s       else v.mp_orden end,
         descripcion    = case when %(t_descripcion)s    then %(descripcion)s    else v.descripcion end,
         guia           = case when %(t_guia)s           then %(guia)s           else v.guia end,
         paqueteria     = case when %(t_paqueteria)s     then %(paqueteria)s     else v.paqueteria end,
         fecha_venta    = case when %(t_fecha_venta)s    then %(fecha_venta)s::timestamptz
                               else v.fecha_venta end,
         entrega_limite = case when %(t_entrega_limite)s then %(entrega_limite)s::timestamptz
                               else v.entrega_limite end,
         moneda         = case when %(t_moneda)s         then %(moneda)s         else v.moneda end,
         total          = case when %(t_total)s          then %(total)s::numeric else v.total end,
         comision       = case when %(t_comision)s       then %(comision)s::numeric
                               else v.comision end,
         precio_origen  = case when %(t_precio_origen)s  then %(precio_origen)s
                               else v.precio_origen end,
         rev            = v.rev + 1
   where v.id = %(id)s and v.rev = %(rev)s and v.estado = 'borrador' and v.borrada_at is null
  returning v.id
), x as materialized (
  select * from jsonb_to_recordset(%(lineas)s::jsonb)
         as x(linea int, sku citext, titulo text, imagen text, cantidad int,
              precio_unitario numeric, almacen text)
), d as (
  delete from ventas.ov_lineas l using o
   where %(con_lineas)s and l.orden_id = o.id
     and not exists (select 1 from x
                      where x.sku = l.sku and x.almacen is not distinct from l.almacen)
  returning l.id
), u as (
  update ventas.ov_lineas l
     set linea = x.linea, titulo = x.titulo, imagen = x.imagen, cantidad = x.cantidad,
         precio_unitario = x.precio_unitario
    from o, x
   where %(con_lineas)s and l.orden_id = o.id
     and l.sku = x.sku and l.almacen is not distinct from x.almacen
  returning l.id
), i as (
  insert into ventas.ov_lineas (orden_id, linea, sku, titulo, imagen, cantidad, precio_unitario,
                             almacen)
  select o.id, x.linea, x.sku, x.titulo, x.imagen, x.cantidad, x.precio_unitario, x.almacen
    from o, x
   where %(con_lineas)s
     and not exists (select 1 from ventas.ov_lineas l
                      where l.orden_id = o.id and l.sku = x.sku
                        and l.almacen is not distinct from x.almacen)
  returning id
), msg as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select o.id, 'sistema', 'borrador_guardado', %(cuerpo)s, %(datos)s::jsonb, %(q)s, %(nombre)s,
         %(via)s
    from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'ov_no_esta_en_borrador_o_cambio_rev')
     + ops.exigir(not %(con_lineas)s
                  or (select count(*) from u) + (select count(*) from i)
                     = jsonb_array_length(%(lineas)s::jsonb), 'renglones_no_cuadran')
     + ops.exigir((select count(*) from msg) = 1, 'guardar_escrituras_no_cuadran') as cuadra
""")


def _igual(campo: str, antes: Any, despues: Any) -> bool:
    if campo in ("total", "comision"):
        return _d(antes) == _d(despues)
    if campo in _FECHAS:
        return _fecha(antes, campo) == despues
    return (antes if antes != "" else None) == despues


def _huella_lineas(lineas: list[dict[str, Any]]) -> list[tuple]:
    return [(str(r["sku"]).lower(), r.get("almacen") or None, int(r["cantidad"]),
             _d(r["precio_unitario"]), r.get("titulo") or None, r.get("imagen") or None)
            for r in lineas]


def _dif_lineas(antes: list[dict[str, Any]], despues: list[dict[str, Any]]) -> dict[str, Any]:
    def llave(r: dict[str, Any]) -> tuple[str, str | None]:
        return str(r["sku"]).lower(), r.get("almacen") or None

    a = {llave(r): r for r in antes}
    d = {llave(r): r for r in despues}
    cambiados = []
    for k, r in d.items():
        if k not in a:
            continue
        c: dict[str, Any] = {"sku": r["sku"]}
        if int(a[k]["cantidad"]) != int(r["cantidad"]):
            c["cantidad"] = [int(a[k]["cantidad"]), int(r["cantidad"])]
        if _d(a[k]["precio_unitario"]) != _d(r["precio_unitario"]):
            c["precio_unitario"] = [float(_d(a[k]["precio_unitario"])),
                                    float(_d(r["precio_unitario"]))]
        if len(c) > 1:
            cambiados.append(c)
    return {"agregados": [r["sku"] for k, r in d.items() if k not in a],
            "quitados": [r["sku"] for k, r in a.items() if k not in d],
            "cambiados": cambiados}


def _cuerpo_guardada(cambios: dict[str, Any], dif: dict[str, Any] | None) -> str:
    partes = []
    if cambios:
        partes.append(", ".join(_ETIQUETA.get(c, c) for c in cambios))
    if dif is not None:
        r = []
        if dif["agregados"]:
            r.append(f"{len(dif['agregados'])} agregado(s)")
        if dif["quitados"]:
            r.append(f"{len(dif['quitados'])} quitado(s)")
        if dif["cambiados"]:
            r.append(f"{len(dif['cambiados'])} con cambios")
        partes.append("renglones: " + (", ".join(r) if r else "reordenados"))
    return "Borrador guardado · " + "; ".join(partes)


def guardar(orden_id: int, rev: int, datos: dict[str, Any], quien: Quien,
            cur: Any = None) -> dict[str, Any]:
    """Guarda un BORRADOR: encabezado y renglones. Lo que no se manda no se toca.

    Fuera de borrador el contenido es inmutable (lo impone la base): no hay
    «editar una confirmada». Quien lo intenta recibe un 400 que dice qué hacer
    (cancelar, o que un administrador la borre), nunca un 500."""
    if not isinstance(datos, dict):
        raise Invalido("El cuerpo de la orden no es válido.")
    firma = _firma(quien)
    o = _preparar(orden_id, rev, quien, "editar", cur)
    nuevos = _encabezado(datos, parcial=True)

    actuales = list(o.get("lineas") or [])
    lineas: list[dict[str, Any]] | None = None
    if datos.get("lineas") is not None:
        lineas = _lineas(datos["lineas"], _bodegas_mapa(cur))
        if _huella_lineas(lineas) == _huella_lineas(actuales):
            lineas = None          # la pantalla manda el documento entero: idénticos no es un cambio
    if nuevos.get("total") is _TOTAL_AUTO:
        nuevos["total"] = _suma(lineas if lineas is not None else actuales)

    # La LIGA con la venta: si cambia, lo mismo que en el alta (todo o nada, la
    # cuenta que se completa sola, y una venta FULL no lleva orden propia).
    trio = tuple(nuevos.get(c, o[c]) for c in _MP)
    if any(c in nuevos and not _igual(c, o[c], nuevos[c]) for c in _MP):
        trio = _ligar_venta(*trio, cur)
        for c, valor in zip(_MP, trio):
            nuevos[c] = valor

    cambios = {c: [_plano(o[c]), _plano(v)] for c, v in nuevos.items()
               if c in _CAMPOS and not _igual(c, o[c], v)}
    if not cambios and lineas is None:
        return {"ok": True, "orden": _orden(o, quien, cur), "mensaje": "Sin cambios."}
    if any(c in cambios for c in _MP):
        ligada = _venta_ligada(*trio, excepto=o["id"], cur=cur)
        if ligada:
            raise Conflicto(f"Esa venta ya tiene la orden {ligada['folio']}.")

    op = _op()
    bitacora: dict[str, Any] = {"op": op, "cambios": cambios}
    dif = None
    if lineas is not None:
        dif = _dif_lineas(actuales, lineas)
        bitacora["renglones"] = dif
        bitacora["lineas"] = _lineas_bitacora(lineas)
    params: dict[str, Any] = {**firma, "id": o["id"], "rev": o["rev"],
                              "con_lineas": lineas is not None,
                              "lineas": _lineas_json(lineas or []),
                              "cuerpo": _cuerpo_guardada(cambios, dif), "datos": _json(bitacora)}
    for c in _CAMPOS:
        params[f"t_{c}"] = c in cambios
        params[c] = nuevos[c] if c in cambios else None
    try:
        _transicion("guardar", SQL_GUARDAR, params, cur)
    except _Rechazo as r:
        # Un UPDATE que liga el borrador a una venta que ya tiene orden viva: no
        # es un bug, es la misma venta capturada dos veces.
        if r.es("23505", "ov_ordenes_mp_uq"):
            raise _choque_venta(*trio, excepto=o["id"], cur=cur) from r.exc
        return _fallo(r, "guardar", o["id"], op, quien, "Cambios guardados.", cur)
    return _resp(o["id"], quien, "Cambios guardados.", cur)


# ══════════════════════════════════════════════════════════════════════════════
# Confirmar: aparta TODO o NADA (patrón c de la guía, tal cual)
# ══════════════════════════════════════════════════════════════════════════════

# Lo único que cambia frente al verificador: `confirmada_nombre`, la vía real y
# el mensaje con sus datos. `confirmada_at` TIENE que ser now(): la guarda de
# `ov_lineas` sólo deja poner bodega y apartado en la transacción que confirmó.
#
# Candados, en orden: la orden (CAS de rev) → `alm` FOR SHARE (espera a un acta
# que esté cambiando banderas y vuelve a exigir `admite_ov` DENTRO del candado,
# C11) → `x` FOR UPDATE del saldo en orden (sku, almacen), materializada y leída
# → `s` aparta sólo donde alcanza (`libre >= n`) → `f` pone bodega y apartado
# del renglón en UN update → mensaje → cuatro `exigir`, uno por cosa que debe
# cuadrar. Si un solo renglón no alcanza, `s` trae menos filas que `x`,
# `no_alcanzo` truena y NADA queda apartado.
SQL_CONFIRMAR = _una("""
with o as (
  update ventas.ov_ordenes v
     set estado = 'confirmada', confirmada_at = now(), confirmada_por = %(q)s,
         confirmada_nombre = %(nombre)s, rev = v.rev + 1
   where v.id = %(id)s and v.rev = %(rev)s and v.estado = 'borrador' and v.borrada_at is null
     and (v.tipo <> 'full' or v.envio_ref is not null)
  returning v.id
), plan as materialized (
  select (e->>'id')::bigint as linea_id, e->>'almacen' as almacen
    from jsonb_array_elements(%(plan)s::jsonb) e
), alm as (
  select a.codigo from almacen.almacenes a
   where a.codigo in (select almacen from plan) and a.fuente = 'kubera' and a.admite_ov
     for share
), x as materialized (
  select sa.sku, sa.almacen, li.cantidad as n, li.id as linea_id
    from o
    join ventas.ov_lineas li on li.orden_id = o.id
    join plan on plan.linea_id = li.id
    join alm on alm.codigo = plan.almacen
    join almacen.stock_almacen sa on sa.sku = li.sku and sa.almacen = plan.almacen
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update almacen.stock_almacen sa
     set apartado = sa.apartado + x.n
    from x
   where sa.sku = x.sku and sa.almacen = x.almacen and sa.libre >= x.n
  returning sa.sku, sa.almacen
), f as (
  update ventas.ov_lineas li set almacen = x.almacen, reservado = li.cantidad
    from x where li.id = x.linea_id
  returning li.id
), msg as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select o.id, 'sistema', 'confirmada', %(cuerpo)s, %(datos)s::jsonb, %(q)s, %(nombre)s, %(via)s
    from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'ov_no_esta_en_borrador_o_cambio_rev')
     + ops.exigir((select count(*) from x) > 0
                  and (select count(*) from x) = (select count(*) from ventas.ov_lineas
                                                   where orden_id = %(id)s),
                  'renglon_sin_plan_o_sin_saldo')
     + ops.exigir((select count(*) from s) = (select count(*) from x), 'no_alcanzo')
     + ops.exigir((select count(*) from f) = (select count(*) from x), 'renglones_no_cuadran')
     + ops.exigir((select count(*) from msg) = 1, 'confirmar_escrituras_no_cuadran') as cuadra
""")

# Para EXPLICAR un «no alcanzó»: el plan contra el catálogo y el saldo de ahora.
_SQL_DIAGNOSTICO = """
select l.id, l.sku::text as sku, l.titulo, l.cantidad, p.almacen,
       a.codigo is not null as existe, coalesce(a.fuente = 'kubera', false) as de_kubera,
       coalesce(a.admite_ov, false) as admite_ov,
       sa.sku is not null as con_saldo, sa.libre
  from ventas.ov_lineas l
  left join jsonb_to_recordset(%(plan)s::jsonb) as p(id bigint, almacen text) on p.id = l.id
  left join almacen.almacenes a on a.codigo = p.almacen
  left join almacen.stock_almacen sa on sa.sku = l.sku and sa.almacen = p.almacen
 where l.orden_id = %(id)s
 order by l.linea, l.id
"""

# El mensaje `no_alcanzo` va en OTRA transacción, después del fallo: el KB001
# deshizo la sentencia de confirmar entera, mensaje incluido (guía §4.1 punto 7).
# No mueve `rev`: la orden no cambió.
SQL_MENSAJE_SISTEMA = _una("""
insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
select o.id, 'sistema', %(evento)s, %(cuerpo)s, %(datos)s::jsonb, %(q)s, %(nombre)s, %(via)s
  from ventas.ov_ordenes o
 where o.id = %(id)s and o.borrada_at is null
returning id
""")


def _plan(lineas: list[dict[str, Any]], plan: Any,
          mapa: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """De qué bodega sale CADA renglón: `[{id, almacen}]` para todos. Por omisión,
    la bodega que el renglón ya trae. Lo que la sentencia va a exigir dentro del
    candado se dice aquí antes, con palabras: un renglón sin bodega, o una
    bodega que no admite OV, no llega a la base."""
    pedido: dict[int, str] = {}
    if plan is not None:
        if not isinstance(plan, (list, tuple)):
            raise Invalido("El plan de bodegas debe ser una lista de {id, almacen}.")
        for e in plan:
            lid = _entero_estricto(e.get("id")) if isinstance(e, dict) else None
            alm = _limpio(e.get("almacen")).upper() if isinstance(e, dict) else ""
            if lid is None or not alm:
                raise Invalido("Cada entrada del plan lleva el «id» del renglón y su «almacen».")
            pedido[lid] = alm
    salida = []
    for l in lineas:
        alm = pedido.get(int(l["id"])) or _limpio(l.get("almacen")).upper()
        if not alm:
            raise Invalido(f"El renglón de {l['sku']} no tiene bodega: elige de cuál sale "
                           "antes de confirmar.")
        porque = _porque_no_bodega(alm, mapa)
        if porque:
            raise Invalido(f"{l['sku']}: {porque}.")
        salida.append({"id": int(l["id"]), "almacen": alm})
    return salida


def _texto_no_alcanzo(faltantes: list[dict[str, Any]]) -> str:
    """«No alcanzó el stock para apartar: SKU X pide 3 y hay 1 libre en ENSAYO.»
    Dice CUÁL, cuánto pide y cuánto hay; un SKU sin fila de saldo en esa bodega
    se dice como lo que es («sin existencias registradas»), no como un cero."""
    partes = []
    for f in faltantes[:3]:
        if f.get("libre") is None:
            partes.append(f"{f['sku']} pide {f['pide']} y no tiene existencias registradas "
                          f"en {f['almacen']}")
        else:
            hay = max(0, int(f["libre"]))
            partes.append(f"{f['sku']} pide {f['pide']} y hay {hay} "
                          f"{'libre' if hay == 1 else 'libres'} en {f['almacen']}")
    resto = len(faltantes) - len(partes)
    if resto > 0:
        partes.append(f"y {resto} SKU más" if resto == 1 else f"y otros {resto} SKU")
    return "No alcanzó el stock para apartar: " + "; ".join(partes) + ". No se apartó nada."


def _mensaje_sistema(orden_id: int, evento: str, cuerpo: str, datos: dict[str, Any],
                     firma: dict[str, Any], cur: Any = None) -> None:
    """Deja un mensaje del sistema FUERA de una transición. Nunca lanza: que el
    aviso no se pueda escribir no puede tapar el error que se va a contestar."""
    try:
        _transicion("mensaje_sistema", SQL_MENSAJE_SISTEMA,
                    {**firma, "id": orden_id, "evento": evento, "cuerpo": cuerpo[:MAX_MENSAJE],
                     "datos": _json(datos)}, cur)
    except Exception as exc:  # noqa: BLE001
        log.warning("ordenes_venta: no se pudo dejar el mensaje «%s» en la orden %s (%s)",
                    evento, orden_id, type(exc).__name__)


def _no_alcanzo(o: dict[str, Any], plan: list[dict[str, Any]], firma: dict[str, Any],
                motivo: str, cur: Any = None) -> ErrorOV:
    """La sentencia de confirmar dijo `no_alcanzo` o `renglon_sin_plan_o_sin_saldo`:
    se relee el saldo para decir QUÉ SKU no alcanza y se deja el aviso en el chat."""
    try:
        diag = _filas(_SQL_DIAGNOSTICO, {"id": o["id"], "plan": _json(plan)}, cur)
    except Exception as exc:  # noqa: BLE001 — sin diagnóstico sale el texto general
        log.warning("ordenes_venta.confirmar: no se pudo releer el saldo para explicar "
                    "«%s» (%s)", motivo, type(exc).__name__)
        return Conflicto(texto_de_motivo(motivo))
    faltantes = []
    for d in diag:
        if not d["almacen"]:
            return Invalido(f"El renglón de {d['sku']} no tiene bodega: elige de cuál sale "
                            "antes de confirmar.")
        if not (d["existe"] and d["de_kubera"] and d["admite_ov"]):
            return Invalido(f"{d['sku']}: la bodega {d['almacen']} no admite órdenes de venta.")
        if not d["con_saldo"] or int(d["libre"]) < int(d["cantidad"]):
            faltantes.append({"sku": d["sku"], "titulo": d.get("titulo"),
                              "pide": int(d["cantidad"]), "almacen": d["almacen"],
                              "libre": int(d["libre"]) if d["con_saldo"] else None})
    if not faltantes:
        # Entre el rechazo y esta lectura llegó stock (o lo soltó otra orden).
        return Conflicto("No alcanzó el stock al intentar apartar, pero ya hay: "
                         "vuelve a confirmar.")
    texto = _texto_no_alcanzo(faltantes)
    _mensaje_sistema(o["id"], "no_alcanzo", texto,
                     {"lineas": [{"sku": f["sku"], "titulo": f["titulo"], "cantidad": f["pide"],
                                  "almacen": f["almacen"], "libre": f["libre"], "reservado": 0}
                                 for f in faltantes]}, firma, cur)
    return Conflicto(texto)


def confirmar(orden_id: int, rev: int, quien: Quien, plan: list[dict[str, Any]] | None = None,
              cur: Any = None) -> dict[str, Any]:
    """Borrador → confirmada, APARTANDO cada renglón en su bodega. Todo o nada:
    si un renglón no alcanza no se confirma, no se aparta ninguno, el 409 dice
    qué SKU pide cuánto y cuánto hay, y queda el aviso en el chat de la orden.

    `plan` (opcional) = `[{id, almacen}]`, de qué bodega sale cada renglón; por
    omisión la que el renglón ya trae."""
    firma = _firma(quien)
    o = _preparar(orden_id, rev, quien, "confirmar", cur)
    if o.get("tipo") == "full" and not o.get("envio_ref"):
        raise Invalido("A este envío a FULL le falta su número de envío: sin él no se confirma.")
    lineas = list(o.get("lineas") or [])
    plan_ = _plan(lineas, plan, _bodegas_mapa(cur))
    por_id = {p["id"]: p["almacen"] for p in plan_}
    piezas = sum(int(l["cantidad"]) for l in lineas)
    op = _op()
    # «1 renglón apartado» / «2 renglones apartados»: el participio concuerda.
    apartado = "apartado" if len(lineas) == 1 else "apartados"
    cuerpo = f"Confirmada: {_renglones(len(lineas))} {apartado} ({_piezas(piezas)})"
    datos = {"op": op, "lineas": _lineas_bitacora(
        [{**l, "almacen": por_id[int(l["id"])]} for l in lineas], reservado=True)}
    try:
        _transicion("confirmar", SQL_CONFIRMAR,
                    {**firma, "id": o["id"], "rev": o["rev"], "plan": _json(plan_),
                     "cuerpo": cuerpo, "datos": _json(datos)}, cur)
    except _Rechazo as r:
        if r.clase == "negocio" and r.detalle in ("no_alcanzo", "renglon_sin_plan_o_sin_saldo"):
            raise _no_alcanzo(o, plan_, firma, r.detalle, cur) from r.exc
        return _fallo(r, "confirmar", o["id"], op, quien, cuerpo + ".", cur)
    return _resp(o["id"], quien, cuerpo + ".", cur)


# ══════════════════════════════════════════════════════════════════════════════
# Entregar: por renglón, una vez por renglón, con su salida en el libro (patrón e)
# ══════════════════════════════════════════════════════════════════════════════

# `p` = lo que salió de cada renglón pedido. Cada uno se entrega UNA vez:
# `entregado = n` (0..cantidad) y lo que no salió se suelta (`apartado −
# cantidad`); la `salida_ov` sólo se escribe si n > 0, con la clave del hecho
# (`ov:<orden>:linea:<renglón>:salida`, la misma del «¿salió?»). La orden pasa
# a `entregada` cuando no queda ningún renglón sin entregar; si queda alguno
# sigue `confirmada` (entrega parcial) y `ov_coherente` lo vigila al COMMIT.
#
# Frente al verificador: nombres, vía real, el evento según quede o no algo por
# salir, y el `exigir` MIXTO partido en dos (guía §4.6): `entrega_no_cuadra`
# para lo de negocio (algún renglón pedido ya salió o no aparta: relee) y
# `entrega_escrituras_no_cuadran` para los conteos de escrituras, que es un bug.
# `canal_cancelo_at is null`: con la marca del canal la orden ya no puede pasar
# a entregada (ov_ordenes_canal_cancelo_chk); espera el «¿salió?».
SQL_ENTREGAR = _una("""
with p as materialized (
  select (e->>'id')::bigint as linea_id, (e->>'n')::int as n
    from jsonb_array_elements(%(lineas)s::jsonb) e
), alm as (
  select a.codigo from almacen.almacenes a
   where a.fuente = 'kubera'
     and a.codigo in (select li.almacen from ventas.ov_lineas li join p on p.linea_id = li.id)
     for share
), o as (
  update ventas.ov_ordenes v
     set rev = v.rev + 1,
         estado           = case when t.quedan = 0 then 'entregada' else v.estado end,
         entregada_at     = case when t.quedan = 0 then now() end,
         entregada_por    = case when t.quedan = 0 then %(q)s end,
         entregada_nombre = case when t.quedan = 0 then %(nombre)s end
    from (select count(*) filter (where li.entregado_at is null
                                    and not exists (select 1 from p where p.linea_id = li.id))
                   as quedan
            from ventas.ov_lineas li where li.orden_id = %(id)s) t
   where v.id = %(id)s and v.rev = %(rev)s and v.estado = 'confirmada' and v.borrada_at is null
     and v.canal_cancelo_at is null
     and (v.tipo <> 'full' or v.envio_ref is not null)
  returning v.id, v.folio, v.estado
), l as materialized (
  select li.id, li.sku, li.almacen, li.cantidad, p.n
    from o join ventas.ov_lineas li on li.orden_id = o.id
    join p on p.linea_id = li.id
    join alm on alm.codigo = li.almacen
   where li.entregado_at is null and li.reservado = li.cantidad and p.n between 0 and li.cantidad
), x as materialized (
  select l.id as linea_id, sa.sku, sa.almacen, l.n, l.cantidad
    from l join almacen.stock_almacen sa on sa.sku = l.sku and sa.almacen = l.almacen
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update almacen.stock_almacen sa
     set fisico = sa.fisico - x.n, apartado = sa.apartado - x.cantidad
    from x where sa.sku = x.sku and sa.almacen = x.almacen
  returning sa.sku, sa.almacen, sa.fisico as saldo, x.n, x.linea_id
), m as (
  insert into almacen.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, ov_linea_id, clave,
                             quien, quien_nombre, via)
  select s.sku, s.almacen, -s.n, s.saldo, 'salida_ov', (select folio from o), s.linea_id,
         'ov:' || %(id)s || ':linea:' || s.linea_id || ':salida', %(q)s, %(nombre)s, %(via)s
    from s where s.n > 0
  returning ov_linea_id
), r as (
  update ventas.ov_lineas li
     set reservado = 0, entregado = s.n, entregado_at = now(), entregado_por = %(q)s
    from s where li.id = s.linea_id
  returning li.id
), msg as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select o.id, 'sistema',
         case when o.estado = 'entregada' then 'entregada' else 'entregada_parcial' end,
         case when o.estado = 'entregada' then %(cuerpo_total)s else %(cuerpo_parcial)s end,
         %(datos)s::jsonb, %(q)s, %(nombre)s, %(via)s
    from o
  returning id
)
select (select estado from o) as estado,
       ops.exigir((select count(*) from o) = 1, 'ov_no_esta_confirmada_o_cambio_rev')
     + ops.exigir((select count(*) from s) = (select count(*) from p), 'entrega_no_cuadra')
     + ops.exigir((select count(*) from r) = (select count(*) from s)
                  and (select count(*) from m) = (select count(*) from s where n > 0)
                  and (select count(*) from msg) = 1, 'entrega_escrituras_no_cuadran') as cuadra
""")


def entregar(orden_id: int, rev: int, quien: Quien, lineas: list[dict[str, Any]] | None = None,
             cur: Any = None) -> dict[str, Any]:
    """DELIVERED: almacén entregó a la paquetería. `lineas=[{id, n}]` es lo que
    salió de cada renglón (n de 0 a su cantidad); sin `lineas` salen todos los
    renglones pendientes completos. Cada renglón se entrega una sola vez: las
    piezas que no salieron se sueltan, y las que sí bajan el físico con su
    movimiento `salida_ov` en el libro. Mientras quede un renglón por salir la
    orden sigue confirmada (entrega parcial)."""
    firma = _firma(quien)
    o = _preparar(orden_id, rev, quien, "entregar", cur)
    de_la_orden = list(o.get("lineas") or [])
    salen = _entrega(lineas, de_la_orden)
    if not salen:
        raise Invalido("La orden no tiene renglones por entregar.")
    por_id = {int(l["id"]): l for l in de_la_orden}
    pendientes = [l for l in de_la_orden if l.get("entregado_at") is None]
    salieron = sum(e["n"] for e in salen)
    sueltas = sum(int(por_id[e["id"]]["cantidad"]) - e["n"] for e in salen)
    quedan = len(pendientes) - len(salen)
    if quedan == 0 and int(o["piezas_entregadas"]) + salieron == 0:
        # Quedaría «entregada» sin que haya salido una sola pieza: eso no es una
        # entrega, es una orden que no salió.
        raise Invalido("No salió ninguna pieza: si la orden no va a salir, cancélala.")
    op = _op()
    suelta = f" · se soltaron {_piezas(sueltas)} que no salieron" if sueltas else ""
    cuerpo_total = f"Entregada a la paquetería (DELIVERED) · {_piezas(salieron)}{suelta}"
    cuerpo_parcial = (f"Entrega parcial · {_piezas(salieron)} de {_renglones(len(salen))}"
                      f"{suelta} · {_quedan(quedan)}")
    datos = {"op": op, "lineas": [
        {"sku": por_id[e["id"]]["sku"], "titulo": por_id[e["id"]].get("titulo"),
         "cantidad": int(por_id[e["id"]]["cantidad"]), "entregado": e["n"],
         "almacen": por_id[e["id"]].get("almacen"), "reservado": 0} for e in salen]}
    hecho = ("Entregada a la paquetería." if quedan == 0
             else f"Entrega parcial registrada: {_quedan(quedan)}.")
    try:
        _transicion("entregar", SQL_ENTREGAR,
                    {**firma, "id": o["id"], "rev": o["rev"], "lineas": _json(salen),
                     "cuerpo_total": cuerpo_total, "cuerpo_parcial": cuerpo_parcial,
                     "datos": _json(datos)}, cur)
    except _Rechazo as r:
        return _fallo(r, "entregar", o["id"], op, quien, hecho, cur)
    return _resp(o["id"], quien, hecho, cur)


# ══════════════════════════════════════════════════════════════════════════════
# Cancelar (patrón f) y su hermana: cancelar cuando ya salió alguna pieza
# ══════════════════════════════════════════════════════════════════════════════

# `__GUARDIA__` es lo único que cambia entre la cancelación de una PERSONA (CAS
# de `rev`) y la del CANAL (CAS por estado, sin rev: guía §4.2). Las constantes
# se arman al importar; en tiempo de ejecución el texto es fijo.
#
# SQL_CANCELAR es el patrón (f) tal cual: desde borrador o confirmada, suelta
# EXACTAMENTE lo reservado de cada renglón que no ha salido. Si algún renglón
# ya entregó piezas truena al COMMIT (23514 ov_coherente): esa cancelación no es
# `cancelada`, es la de abajo.
_CANCELAR = """
with o as (
  update ventas.ov_ordenes v
     set estado = 'cancelada', cancelada_at = now(), cancelada_por = %(q)s,
         cancelada_nombre = %(nombre)s, cancelada_origen = %(origen)s,
         cancelada_motivo = %(motivo)s, rev = v.rev + 1
   where v.id = %(id)s and __GUARDIA__ and v.borrada_at is null
  returning v.id
), x as materialized (
  select sa.sku, sa.almacen, li.reservado as n, li.id as linea_id
    from o
    join ventas.ov_lineas li on li.orden_id = o.id and li.reservado > 0 and li.entregado_at is null
    join almacen.stock_almacen sa on sa.sku = li.sku and sa.almacen = li.almacen
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update almacen.stock_almacen sa set apartado = sa.apartado - x.n
    from x where sa.sku = x.sku and sa.almacen = x.almacen
  returning sa.sku
), r as (
  update ventas.ov_lineas li set reservado = 0 from x where li.id = x.linea_id returning li.id
), msg as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select o.id, 'sistema', 'cancelada', %(cuerpo)s, %(datos)s::jsonb, %(q)s, %(nombre)s, %(via)s
    from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'ov_no_cancelable_o_cambio_rev')
     + ops.exigir((select count(*) from s) = (select count(*) from x)
                  and (select count(*) from r) = (select count(*) from x)
                  and (select count(*) from msg) = 1, 'cancelar_no_cuadra') as cuadra
"""
SQL_CANCELAR = _una(_CANCELAR.replace(
    "__GUARDIA__", "v.rev = %(rev)s and v.estado in ('borrador', 'confirmada')"))
# El canal sólo cancela una CONFIRMADA que no espera el «¿salió?» (con la marca
# puesta decide Bodega, no el sondeo).
SQL_CANCELAR_CANAL = _una(_CANCELAR.replace(
    "__GUARDIA__", "v.estado = 'confirmada' and v.canal_cancelo_at is null"))

# Cancelar una confirmada de la que YA SALIERON piezas (entrega parcial): queda
# `entregada_cancelada`, la única que admite devolución. El verificador no trae
# esta sentencia; es la misma forma que (f) con lo que piden las restricciones:
#   · `entregada_*` (el estado lo exige: ov_ordenes_entr_chk). Se toma de la
#     ÚLTIMA entrega real de sus renglones —cuándo y quién sacó las piezas—, no
#     de quien cancela ni de la hora de la cancelación. `e` es además la guarda:
#     sin un renglón con piezas afuera no hay fila que actualizar.
#   · `cancelada_*` con su motivo (≥ 5: estuvo confirmada).
#   · suelta el apartado de lo que NO salió, y `devolucion_estado = 'pendiente'`.
_CANCELAR_CON_SALIDA = """
with o as (
  update ventas.ov_ordenes v
     set estado = 'entregada_cancelada',
         entregada_at = e.entregado_at, entregada_por = e.entregado_por,
         cancelada_at = now(), cancelada_por = %(q)s, cancelada_nombre = %(nombre)s,
         cancelada_origen = %(origen)s, cancelada_motivo = %(motivo)s,
         devolucion_estado = coalesce(v.devolucion_estado, 'pendiente'), rev = v.rev + 1
    from (select li.entregado_at, li.entregado_por
            from ventas.ov_lineas li
           where li.orden_id = %(id)s and li.entregado > 0
           order by li.entregado_at desc, li.id desc
           limit 1) e
   where v.id = %(id)s and __GUARDIA__ and v.estado = 'confirmada' and v.borrada_at is null
  returning v.id
), x as materialized (
  select sa.sku, sa.almacen, li.reservado as n, li.id as linea_id
    from o
    join ventas.ov_lineas li on li.orden_id = o.id and li.reservado > 0 and li.entregado_at is null
    join almacen.stock_almacen sa on sa.sku = li.sku and sa.almacen = li.almacen
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update almacen.stock_almacen sa set apartado = sa.apartado - x.n
    from x where sa.sku = x.sku and sa.almacen = x.almacen
  returning sa.sku
), r as (
  update ventas.ov_lineas li set reservado = 0 from x where li.id = x.linea_id returning li.id
), msg as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select o.id, 'sistema', 'devolucion_esperada', %(cuerpo)s, %(datos)s::jsonb, %(q)s,
         %(nombre)s, %(via)s
    from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'ov_no_cancelable_o_cambio_rev')
     + ops.exigir((select count(*) from s) = (select count(*) from x)
                  and (select count(*) from r) = (select count(*) from x)
                  and (select count(*) from msg) = 1, 'cancelar_no_cuadra') as cuadra
"""
SQL_CANCELAR_CON_SALIDA = _una(_CANCELAR_CON_SALIDA.replace("__GUARDIA__", "v.rev = %(rev)s"))
SQL_CANCELAR_CON_SALIDA_CANAL = _una(_CANCELAR_CON_SALIDA.replace(
    "__GUARDIA__", "v.canal_cancelo_at is null"))


def _con_motivo(texto: str, motivo: str | None) -> str:
    return f"{texto} · Motivo: {motivo}" if motivo else texto


_QUIEN_CANCELA = {"manual": "Cancelada", "sistema": "Cancelada por el sistema",
                  "marketplace": "Cancelada por el marketplace"}


def _cuerpo_cancelada(origen: str, motivo: str | None, liberadas: int) -> str:
    t = _QUIEN_CANCELA.get(origen, "Cancelada")
    if liberadas:
        t += f" · se soltaron {_piezas(liberadas)}"
    return _con_motivo(t, motivo)


def _cuerpo_cancelada_con_salida(origen: str, motivo: str | None, salidas: int,
                                 liberadas: int) -> str:
    t = (f"{_QUIEN_CANCELA.get(origen, 'Cancelada')} con {_piezas(salidas)} ya "
         f"{'entregada' if salidas == 1 else 'entregadas'} a la paquetería (DELIVERED but "
         "CANCELLED): se espera la devolución")
    if liberadas:
        t += f" · se soltaron {_piezas(liberadas)} que no habían salido"
    return _con_motivo(t, motivo)


def _params_cancelar(o: dict[str, Any], firma: dict[str, Any], motivo: str | None,
                     origen: str, op: str) -> tuple[bool, dict[str, Any], str]:
    """(¿ya salió algo?, parámetros de la sentencia, lo que se le dice a quien pide)."""
    salidas = int(o["piezas_entregadas"])
    liberadas = int(o["piezas_apartadas"])
    datos = {"op": op, "motivo": motivo, "origen": origen, "lineas": [
        {"sku": l["sku"], "titulo": l.get("titulo"), "cantidad": int(l["cantidad"]),
         "almacen": l.get("almacen"), "reservado": int(l.get("reservado") or 0),
         "entregado": l.get("entregado")} for l in (o.get("lineas") or [])]}
    if salidas > 0:
        cuerpo = _cuerpo_cancelada_con_salida(origen, motivo, salidas, liberadas)
        hecho = ("Cancelada con piezas ya entregadas: queda como entregada y cancelada, "
                 "en espera de la devolución.")
    else:
        cuerpo = _cuerpo_cancelada(origen, motivo, liberadas)
        hecho = "Orden cancelada." + (f" Se soltaron {_piezas(liberadas)}." if liberadas else "")
    return salidas > 0, {**firma, "id": o["id"], "rev": o["rev"], "origen": origen,
                         "motivo": motivo, "cuerpo": cuerpo, "datos": _json(datos)}, hecho


def _cancelar(o: dict[str, Any], quien: Quien, motivo: Any, origen: str, operacion: str,
              cur: Any = None) -> dict[str, Any]:
    """La cancelación de una PERSONA (CAS de rev), ya con el permiso exigido."""
    firma = _firma(quien)
    estuvo_confirmada = bool(o.get("confirmada_at"))
    motivo_ = _motivo_texto(
        motivo, MIN_MOTIVO_CANCELAR if estuvo_confirmada else 0,
        f"Escribe por qué se cancela la orden ({MIN_MOTIVO_CANCELAR} caracteres o más).")
    op = _op()
    con_salida, params, hecho = _params_cancelar(o, firma, motivo_, origen, op)
    try:
        _transicion(operacion, SQL_CANCELAR_CON_SALIDA if con_salida else SQL_CANCELAR,
                    params, cur)
    except _Rechazo as r:
        return _fallo(r, operacion, o["id"], op, quien, hecho, cur)
    return _resp(o["id"], quien, hecho, cur)


def cancelar(orden_id: int, rev: int, quien: Quien, motivo: str = "", origen: str = "manual",
             cur: Any = None) -> dict[str, Any]:
    """Cancela. Un borrador o una confirmada sin salidas → `cancelada` (suelta
    exactamente lo apartado). Una confirmada de la que ya salió alguna pieza →
    `entregada_cancelada`, con la devolución pendiente. Si estuvo confirmada el
    motivo es obligatorio (5 caracteres o más). Nunca depende de la bandera:
    soltar stock es seguro con el módulo apagado."""
    if origen not in ORIGENES_CANCELACION:
        raise Invalido("«origen» es 'manual', 'sistema' o 'marketplace'.")
    o = _preparar(orden_id, rev, quien, "cancelar", cur)
    return _cancelar(o, quien, motivo, origen, "cancelar", cur)


# ══════════════════════════════════════════════════════════════════════════════
# Borrar (lógico, sólo admin)
# ══════════════════════════════════════════════════════════════════════════════

# Una orden NO se borra (el trigger rechaza el DELETE): se marca, con quién y
# por qué, y el folio no se recicla. Si aparta, se suelta EN LA MISMA sentencia:
# `ov_coherente` exige al COMMIT que una borrada no aparte. Borrada, ya no
# cambia nada de ella y su venta y su clave quedan libres.
SQL_BORRAR = _una("""
with o as (
  update ventas.ov_ordenes v
     set borrada_at = now(), borrada_por = %(q)s, borrada_nombre = %(nombre)s,
         borrada_motivo = %(motivo)s, rev = v.rev + 1
   where v.id = %(id)s and v.rev = %(rev)s and v.borrada_at is null
  returning v.id
), x as materialized (
  select sa.sku, sa.almacen, li.reservado as n, li.id as linea_id
    from o
    join ventas.ov_lineas li on li.orden_id = o.id and li.reservado > 0 and li.entregado_at is null
    join almacen.stock_almacen sa on sa.sku = li.sku and sa.almacen = li.almacen
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update almacen.stock_almacen sa set apartado = sa.apartado - x.n
    from x where sa.sku = x.sku and sa.almacen = x.almacen
  returning sa.sku
), r as (
  update ventas.ov_lineas li set reservado = 0 from x where li.id = x.linea_id returning li.id
), msg as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select o.id, 'sistema', 'borrada_admin', %(cuerpo)s, %(datos)s::jsonb, %(q)s, %(nombre)s,
         %(via)s
    from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'ov_no_borrable_o_cambio_rev')
     + ops.exigir((select count(*) from s) = (select count(*) from x)
                  and (select count(*) from r) = (select count(*) from x)
                  and (select count(*) from msg) = 1, 'borrar_escrituras_no_cuadran') as cuadra
""")


def borrar(orden_id: int, rev: int, quien: Quien, motivo: str, cur: Any = None) -> dict[str, Any]:
    """Admin: «borra» la orden. No se elimina: queda quién, cuándo y por qué (10
    caracteres o más) y el folio no se recicla. Si apartaba, suelta en la misma
    sentencia. Es la salida para una confirmada capturada mal: su contenido ya
    no se puede editar."""
    firma = _firma(quien)
    o = _preparar(orden_id, rev, quien, "borrar", cur)
    motivo_ = _motivo_texto(motivo, MIN_MOTIVO_BORRAR,
                            f"Escribe por qué se borra la orden ({MIN_MOTIVO_BORRAR} "
                            "caracteres o más).")
    liberadas = int(o["piezas_apartadas"])
    cuerpo = "Orden borrada por un administrador"
    if liberadas:
        cuerpo += f" · se soltaron {_piezas(liberadas)}"
    op = _op()
    datos = {"op": op, "motivo": motivo_, "lineas": [
        {"sku": l["sku"], "titulo": l.get("titulo"), "cantidad": int(l["cantidad"]),
         "almacen": l.get("almacen"), "reservado": int(l.get("reservado") or 0)}
        for l in (o.get("lineas") or [])]}
    try:
        _transicion("borrar", SQL_BORRAR,
                    {**firma, "id": o["id"], "rev": o["rev"], "motivo": motivo_,
                     "cuerpo": _con_motivo(cuerpo, motivo_), "datos": _json(datos)}, cur)
    except _Rechazo as r:
        return _fallo(r, "borrar", o["id"], op, quien, "Orden borrada.", cur)
    return _resp(o["id"], quien, "Orden borrada.", cur)


# ══════════════════════════════════════════════════════════════════════════════
# El canal cancela (patrones f y g de la guía): CAS por ESTADO, sin rev
# ══════════════════════════════════════════════════════════════════════════════

# Paso 1 del «¿salió?»: el canal canceló con el paquete ya en camino. La marca
# NO suelta el apartado: la orden sigue confirmada hasta que Bodega conteste.
# Los dos filtros de más frente a la prueba (`canal_cancelo_at is null` y
# `borrada_at is null`) son los que hacen que ver OTRA VEZ la misma cancelación
# dé un KB001 con nombre y no un 42501 que se leería como bug en cada sondeo.
SQL_CANAL_MARCA = _una("""
with o as (
  update ventas.ov_ordenes
     set canal_cancelo_at = now(), canal_cancelo_ref = %(ref_canal)s, rev = rev + 1
   where id = %(id)s and estado = 'confirmada' and canal_cancelo_at is null
     and borrada_at is null
  returning id
), m as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select id, 'sistema', 'canal_cancelo', %(cuerpo)s, %(datos)s::jsonb, %(q)s, %(nombre)s, %(via)s
    from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'canal_cancelo_no_aplica')
     + ops.exigir((select count(*) from m) = (select count(*) from o),
                  'canal_cancelo_escrituras_no_cuadran') as cuadra
""")

# El canal cancela una orden YA entregada: la pieza salió, así que queda
# `entregada_cancelada` con devolución pendiente. No hay renglones que tocar.
# `coalesce`: si ya tenía `devolucion_estado` (p. ej. recibida) no se regresa a
# pendiente (la base lo rechaza: la llegada no se deshace).
SQL_CANAL_CANCELO_ENTREGADA = _una("""
with o as (
  update ventas.ov_ordenes v
     set estado = 'entregada_cancelada', cancelada_at = now(), cancelada_por = %(q)s,
         cancelada_nombre = %(nombre)s, cancelada_origen = 'marketplace',
         cancelada_motivo = %(motivo)s,
         devolucion_estado = coalesce(v.devolucion_estado, 'pendiente'), rev = v.rev + 1
   where v.id = %(id)s and v.estado = 'entregada' and v.borrada_at is null
  returning v.id
), m as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select o.id, 'sistema', 'devolucion_esperada', %(cuerpo)s, %(datos)s::jsonb, %(q)s, %(nombre)s,
         %(via)s
    from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'canal_cancelo_entregada_no_aplica')
     + ops.exigir((select count(*) from m) = (select count(*) from o),
                  'canal_cancelo_escrituras_no_cuadran') as cuadra
""")

_INTENTOS_CANAL = 3


def canal_cancelo(orden_id: int, ref_canal: str | None, motivo: str | None, en_camino: bool,
                  cur: Any = None) -> dict[str, Any]:
    """El CANAL canceló la venta de esta orden. Lo llama el barrido de
    cancelaciones (sondeo y webhook, que se REPITEN): ver la misma cancelación
    dos veces es éxito, no error. Sin `rev`: compare-and-set por estado, firmado
    `automatico`. Devuelve `{resultado, orden}`:

      cancelada            confirmada sin salir → `cancelada` (soltó el apartado)
      marcada              confirmada y `en_camino` → la marca `canal_cancelo`; la
                           orden queda esperando que Bodega conteste «¿salió?»
      entregada_cancelada  entregada (o confirmada con piezas ya entregadas) →
                           `entregada_cancelada`, devolución pendiente
      ya_marcada           ya tenía la marca: la misma cancelación, vista otra vez
      ya_cancelada         ya estaba cancelada o entregada y cancelada
      nada                 borrada, o todavía en borrador (no hay qué soltar)

    `ref_canal` es el estado del canal al cancelar (IN_TRANSIT, 4 o 5 de Temu…).
    No depende de la bandera: quién corre el barrido lo decide el barrido."""
    _exigir_tablas(cur)
    firma = _firma(AUTOMATICO)
    ref = _limpio(ref_canal)[:80] or ("en_camino" if en_camino else "cancelada")
    motivo_ = _limpio(motivo)[:MAX_MOTIVO]
    if len(motivo_) < MIN_MOTIVO_CANCELAR:
        motivo_ = "Cancelada en el canal" + (f" ({ref})" if _limpio(ref_canal) else "")

    def fin(resultado: str, fila: dict[str, Any] | None = None) -> dict[str, Any]:
        fila = fila if fila is not None else _leer_id(orden_id, cur)
        return {"resultado": resultado, "orden": _orden(fila, AUTOMATICO, cur)}

    for _ in range(_INTENTOS_CANAL):
        o = _leer_id(orden_id, cur)
        estado = o["estado"]
        if o.get("borrada_at") or estado == "borrador":
            return fin("nada", o)
        if estado in ("cancelada", "entregada_cancelada"):
            return fin("ya_cancelada", o)
        if estado == "confirmada" and o.get("canal_cancelo_at"):
            return fin("ya_marcada", o)
        op = _op()
        base = {"op": op, "ref_canal": ref}
        try:
            if estado == "entregada":
                _transicion("canal_cancelo", SQL_CANAL_CANCELO_ENTREGADA, {
                    **firma, "id": o["id"], "motivo": motivo_,
                    "cuerpo": _con_motivo("El canal canceló una venta ya entregada: se espera "
                                          "la devolución", motivo_),
                    "datos": _json({**base, "motivo": motivo_})}, cur)
                return fin("entregada_cancelada")
            if en_camino:
                _transicion("canal_cancelo", SQL_CANAL_MARCA, {
                    **firma, "id": o["id"], "ref_canal": ref,
                    "cuerpo": (f"El canal canceló con el paquete en camino ({ref}): "
                               "¿salió de la bodega?"),
                    "datos": _json({**base, "motivo": motivo_})}, cur)
                return fin("marcada")
            con_salida, params, _hecho = _params_cancelar(o, firma, motivo_, "marketplace", op)
            _transicion("canal_cancelo",
                        SQL_CANCELAR_CON_SALIDA_CANAL if con_salida else SQL_CANCELAR_CANAL,
                        params, cur)
            return fin("entregada_cancelada" if con_salida else "cancelada")
        except _Rechazo as r:
            # 0 filas (KB001) = la orden se movió entre la lectura y el CAS; y un
            # `ov_coherente` al COMMIT = salió un renglón justo en medio. Ninguno
            # se pisa: se RELEE y se decide de nuevo (reintento acotado).
            if r.clase == "negocio" or r.es("23514", "ov_coherente"):
                continue
            # La misma carrera, con otra cara: una entrega PARCIAL que confirma
            # mientras el CAS espera la fila deja la orden `confirmada` y sin
            # marca, así que la guardia por estado vuelve a cumplirse, pero los
            # renglones se leen con la foto de antes (aún «apartados»). La base
            # lo frena —el saldo no puede soltar lo que ya salió, o el renglón
            # entregado ya no cambia— y lo deshace todo. Sólo es «se movió» si
            # la `rev` de verdad cambió desde la lectura de este intento; con la
            # misma rev es una sentencia mal hecha y sale como tal.
            if ((r.es("23514", "stock_almacen_apartado_chk")
                 or r.es("42501", "ov_lineas_inmutable"))
                    and _leer_id(orden_id, cur)["rev"] != o["rev"]):
                continue
            raise _error_de_rechazo(r, "canal_cancelo") from r.exc
    raise Conflicto("La orden se está moviendo en este momento; la cancelación del canal se "
                    "vuelve a intentar en la siguiente pasada.")


# Paso 2 del «¿salió?», cuando Bodega dice que SÍ: la orden sale a
# `entregada_cancelada` con su entrega (de quien contesta), su cancelación (del
# canal) y la devolución pendiente; el saldo baja físico Y apartado; la
# `salida_ov` lleva la clave de entregar; el renglón queda con reservado 0 y sus
# piezas entregadas. Los renglones que ya habían salido en una entrega parcial
# quedan fuera, y eso es lo correcto. Sale de la prueba
# `salio_canal_cancelo_con_paquete_enviado`, más el CAS de rev (contesta una
# persona), nombres, mensaje y el `exigir` partido en dos.
SQL_SALIO = _una("""
with o as (
  update ventas.ov_ordenes v
     set estado = 'entregada_cancelada', entregada_at = now(), entregada_por = %(q)s,
         entregada_nombre = %(nombre)s,
         cancelada_at = now(), cancelada_por = %(q_canal)s, cancelada_nombre = %(nombre_canal)s,
         cancelada_origen = 'marketplace', cancelada_motivo = %(motivo)s,
         devolucion_estado = 'pendiente', rev = v.rev + 1
   where v.id = %(id)s and v.rev = %(rev)s and v.estado = 'confirmada'
     and v.canal_cancelo_at is not null and v.borrada_at is null
  returning v.id, v.folio
), x as materialized (
  select li.id as linea_id, sa.sku, sa.almacen, li.cantidad
    from o
    join ventas.ov_lineas li on li.orden_id = o.id and li.entregado_at is null
                         and li.reservado = li.cantidad
    join almacen.stock_almacen sa on sa.sku = li.sku and sa.almacen = li.almacen
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update almacen.stock_almacen sa
     set fisico = sa.fisico - x.cantidad, apartado = sa.apartado - x.cantidad
    from x where sa.sku = x.sku and sa.almacen = x.almacen
  returning sa.sku, sa.almacen, sa.fisico, x.cantidad as n, x.linea_id
), m as (
  insert into almacen.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, ov_linea_id, clave,
                             quien, quien_nombre, via)
  select s.sku, s.almacen, -s.n, s.fisico, 'salida_ov', (select folio from o), s.linea_id,
         'ov:' || %(id)s || ':linea:' || s.linea_id || ':salida', %(q)s, %(nombre)s, %(via)s
    from s
  returning id
), r as (
  update ventas.ov_lineas li
     set reservado = 0, entregado = s.n, entregado_at = now(), entregado_por = %(q)s
    from s where li.id = s.linea_id
  returning li.id
), msg as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select o.id, 'sistema', 'devolucion_esperada', %(cuerpo)s, %(datos)s::jsonb, %(q)s, %(nombre)s,
         %(via)s
    from o
  returning id
)
select ops.exigir((select count(*) from o) = 1, 'salio_no_cuadra')
     + ops.exigir((select count(*) from x) > 0
                  and (select count(*) from s) = (select count(*) from x)
                  and (select count(*) from m) = (select count(*) from x)
                  and (select count(*) from r) = (select count(*) from x)
                  and (select count(*) from msg) = 1, 'salio_escrituras_no_cuadran') as cuadra
""")


def responder_salio(orden_id: int, rev: int, quien: Quien, salio: bool,
                    cur: Any = None) -> dict[str, Any]:
    """La respuesta de Bodega al «¿salió?» de una orden que el canal canceló con
    el paquete en camino.

      · `salio=True`  → `entregada_cancelada`: se registra la salida (físico y
        libro) y queda la devolución pendiente.
      · `salio=False` → cancelación normal con origen `marketplace`: suelta el
        apartado. (Si otros renglones ya habían salido en una entrega parcial,
        queda `entregada_cancelada` igual: esas piezas sí están afuera.)"""
    if not isinstance(salio, bool):
        raise Invalido("«salio» es verdadero (sí salió) o falso (no salió).")
    firma = _firma(quien)
    o = _preparar(orden_id, rev, quien, "responder_salio", cur)
    ref = o.get("canal_cancelo_ref") or "en camino"
    if not salio:
        return _cancelar(o, quien, f"El canal canceló ({ref}) y Bodega confirmó que el "
                         "paquete NO salió", "marketplace", "responder_salio", cur)
    op = _op()
    salen = [l for l in (o.get("lineas") or []) if l.get("entregado_at") is None]
    piezas = sum(int(l["cantidad"]) for l in salen)
    cuerpo = (f"Bodega: el paquete SÍ salió ({_piezas(piezas)}). El canal lo canceló en camino "
              f"({ref}): queda entregada y cancelada, en espera de la devolución")
    datos = {"op": op, "ref_canal": ref, "lineas": [
        {"sku": l["sku"], "titulo": l.get("titulo"), "cantidad": int(l["cantidad"]),
         "entregado": int(l["cantidad"]), "almacen": l.get("almacen"), "reservado": 0}
        for l in salen]}
    hecho = "Registrado: sí salió. Queda entregada y cancelada, en espera de la devolución."
    try:
        _transicion("responder_salio", SQL_SALIO, {
            **firma, "id": o["id"], "rev": o["rev"], "q_canal": AUTOMATICO.actor,
            "nombre_canal": AUTOMATICO.nombre,
            "motivo": f"Cancelada en el canal con el paquete ya enviado ({ref})",
            "cuerpo": cuerpo, "datos": _json(datos)}, cur)
    except _Rechazo as r:
        return _fallo(r, "responder_salio", o["id"], op, quien, hecho, cur)
    return _resp(o["id"], quien, hecho, cur)


# ══════════════════════════════════════════════════════════════════════════════
# «¿Salió?» TARDÍO (patrón h): la caja de una orden ya CANCELADA sí había salido
# ══════════════════════════════════════════════════════════════════════════════

# SQL_SALIO_TARDE del verificador, más el CAS de rev (lo hace un administrador),
# nombres y el `exigir` partido en dos. La orden pasa de `cancelada` a
# `entregada_cancelada`; el físico baja lo que pedía cada renglón sin entrega
# (ya no había apartado: se soltó al cancelar) con su `salida_ov` y la clave de
# entregar. Después viene la devolución normal.
#
# DOS ERRORES QUE AQUÍ SIGNIFICAN OTRA COSA (guía §4.6):
#   · 23514 ov_ordenes_conf_chk: esa orden se canceló siendo borrador; nunca
#     estuvo confirmada y no pudo salir.
#   · 23505 de la clave o de la venta: al salir de `cancelada` la orden VUELVE a
#     ocupar su venta, y esa venta ya tiene OTRA orden viva (la cancelada la
#     había soltado). NO es «ya existía»: la salida no se escribió.
SQL_SALIO_TARDE = _una("""
with o as (
  update ventas.ov_ordenes v
     set estado = 'entregada_cancelada', entregada_at = now(), entregada_por = %(q)s,
         entregada_nombre = %(nombre)s, devolucion_estado = 'pendiente', rev = v.rev + 1
   where v.id = %(id)s and v.rev = %(rev)s and v.estado = 'cancelada' and v.borrada_at is null
  returning v.id, v.folio
), x as materialized (
  select li.id as linea_id, sa.sku, sa.almacen, li.cantidad
    from o join ventas.ov_lineas li on li.orden_id = o.id and li.entregado_at is null
    join almacen.stock_almacen sa on sa.sku = li.sku and sa.almacen = li.almacen
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update almacen.stock_almacen sa set fisico = sa.fisico - x.cantidad
    from x where sa.sku = x.sku and sa.almacen = x.almacen
  returning sa.sku, sa.almacen, sa.fisico, x.cantidad as n, x.linea_id
), m as (
  insert into almacen.stock_mov (sku, almacen, delta, saldo_despues, motivo, ref, ov_linea_id, clave,
                             quien, quien_nombre, via)
  select s.sku, s.almacen, -s.n, s.fisico, 'salida_ov', (select folio from o), s.linea_id,
         'ov:' || %(id)s || ':linea:' || s.linea_id || ':salida', %(q)s, %(nombre)s, %(via)s
    from s
  returning id
), r as (
  update ventas.ov_lineas li set entregado = s.n, entregado_at = now(), entregado_por = %(q)s
    from s where li.id = s.linea_id
  returning li.id
), msg as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select o.id, 'sistema', 'devolucion_esperada', %(cuerpo)s, %(datos)s::jsonb, %(q)s, %(nombre)s,
         %(via)s
    from o
  returning id
)
select ops.exigir((select count(*) from o) = 1 and (select count(*) from x) > 0,
                  'salio_tarde_no_cuadra')
     + ops.exigir((select count(*) from s) = (select count(*) from x)
                  and (select count(*) from m) = (select count(*) from x)
                  and (select count(*) from r) = (select count(*) from x)
                  and (select count(*) from msg) = 1, 'salio_tarde_escrituras_no_cuadran')
       as cuadra
""")


def salio_tarde(orden_id: int, rev: int, quien: Quien, cur: Any = None) -> dict[str, Any]:
    """Admin: una orden ya CANCELADA cuyo paquete sí había salido (el canal
    canceló en un estado que no parecía «en camino» y Bodega ya lo había
    entregado). Registra la salida y la deja `entregada_cancelada`, en espera de
    la devolución. Sólo si la orden estuvo confirmada."""
    firma = _firma(quien)
    o = _preparar(orden_id, rev, quien, "salio_tarde", cur)
    salen = [l for l in (o.get("lineas") or []) if l.get("entregado_at") is None]
    if not salen:
        raise Invalido("A esa orden no le quedan renglones por registrar como salidos.")
    piezas = sum(int(l["cantidad"]) for l in salen)
    op = _op()
    cuerpo = (f"Bodega: el paquete de esta orden cancelada SÍ había salido ({_piezas(piezas)}): "
              "queda entregada y cancelada, en espera de la devolución")
    datos = {"op": op, "lineas": [
        {"sku": l["sku"], "titulo": l.get("titulo"), "cantidad": int(l["cantidad"]),
         "entregado": int(l["cantidad"]), "almacen": l.get("almacen"), "reservado": 0}
        for l in salen]}
    hecho = "Salida registrada: queda entregada y cancelada, en espera de la devolución."
    try:
        _transicion("salio_tarde", SQL_SALIO_TARDE,
                    {**firma, "id": o["id"], "rev": o["rev"], "cuerpo": cuerpo,
                     "datos": _json(datos)}, cur)
    except _Rechazo as r:
        if r.es("23505", "ov_ordenes_clave_uq", "ov_ordenes_mp_uq"):
            # NUNCA «ya existía» (guía §4.6): la salida NO se escribió y la bodega
            # tiene en el libro piezas que ya no están. Se avisa y se resuelve a mano.
            otra = None
            try:
                otra = _venta_ligada(o.get("mp_canal"), o.get("mp_cuenta"), o.get("mp_orden"),
                                     excepto=o["id"], cur=cur)
            except Exception:  # noqa: BLE001 — el aviso sale igual, sin el folio
                pass
            log.error("ordenes_venta.salio_tarde: la orden %s no pudo registrar su salida "
                      "tardía: su venta ya tiene OTRA orden viva (%s, regla %s). El libro "
                      "tiene piezas de más: resolver a mano.", o["folio"],
                      (otra or {}).get("folio") or "sin identificar", r.detalle)
            raise Conflicto(
                "Esa venta ya tiene otra orden viva"
                + (f" ({otra['folio']})" if otra else "")
                + ": la salida NO se registró. Hay que resolverlo a mano (por ejemplo, "
                  "cancelando antes la orden nueva).") from r.exc
        return _fallo(r, "salio_tarde", o["id"], op, quien, hecho, cur)
    return _resp(o["id"], quien, hecho, cur)


# ══════════════════════════════════════════════════════════════════════════════
# crear_auto (patrón d): la orden de una venta que sale de una bodega de kubera
# ══════════════════════════════════════════════════════════════════════════════

# La FORMA es la del verificador (candados, `previa`, folio y resultado); el
# CONTENIDO va completo en el INSERT porque NO HAY SEGUNDA OPORTUNIDAD: la orden
# nace `confirmada`, y fuera de borrador ya no cambian ni el total, ni la guía,
# ni el precio o el título de un renglón.
#
# EL ORDEN DE CANDADOS ES LA EXCEPCIÓN DOCUMENTADA (guía §4.3): `alm` y el saldo
# primero y `ov_folio` AL FINAL (`alm → x → s → fo`), porque el folio sólo sube
# si no hay orden previa y TODO alcanzó. No hay ciclo: ninguna sentencia toma el
# folio y después el saldo (el alta de borrador no toca saldo). No «corregir».
#
#   · `alm` exige kubera + admite_ov + surte_ventas, DENTRO del candado (C11).
#   · `previa` busca por la clave O por la venta, entre órdenes vivas: repetida
#     devuelve `ya_existia` sin apartar de nuevo.
#   · `x` sólo trae los SKU con fila de saldo; `s` sólo aparta donde alcanza.
#     Si algo falta, `fo` no sube, `o` no nace y `no_alcanzo` deshace todo.
#   · Nace con `creado_via = 'automatico'` y `cliente = mp_canal` (SEG-06: el
#     cliente NUNCA es el comprador; lo exige ov_ordenes_auto_cliente_chk).
SQL_CREAR_AUTO = _una("""
with alm as (
  select a.codigo from almacen.almacenes a
   where a.codigo = %(alm)s and a.fuente = 'kubera' and a.admite_ov and a.surte_ventas
     for share
), previa as (
  select v.id from ventas.ov_ordenes v
   where (v.clave = %(clave)s
          or (v.mp_canal, v.mp_cuenta, v.mp_orden) = (%(mc)s, %(mu)s, %(mo)s))
     and v.borrada_at is null and v.estado <> 'cancelada'
), r as materialized (
  select * from jsonb_to_recordset(%(lineas)s::jsonb)
         as r(linea int, sku citext, n int, precio_unitario numeric, titulo text, imagen text)
), x as materialized (
  select sa.sku, sa.almacen, r.n, r.linea, r.precio_unitario, r.titulo, r.imagen
    from almacen.stock_almacen sa join alm on alm.codigo = sa.almacen join r on r.sku = sa.sku
   where not exists (select 1 from previa)
   order by sa.sku, sa.almacen
     for update of sa
), s as (
  update almacen.stock_almacen sa set apartado = sa.apartado + x.n
    from x where sa.sku = x.sku and sa.almacen = x.almacen and sa.libre >= x.n
  returning sa.sku, sa.almacen
), fo as (
  update ventas.ov_folio set ultimo = ultimo + 1
   where id = 1 and not exists (select 1 from previa)
     and (select count(*) from s) = (select count(*) from r)
  returning ultimo
), o as (
  insert into ventas.ov_ordenes (folio, estado, tipo, cliente, canal, mp_canal, mp_cuenta, mp_orden,
                              descripcion, guia, paqueteria, fecha_venta, entrega_limite, moneda,
                              total, comision, precio_origen, creado_por, creado_nombre,
                              creado_via, clave, confirmada_at, confirmada_por,
                              confirmada_nombre)
  select 'OV-' || lpad(fo.ultimo::text, greatest(5, length(fo.ultimo::text)), '0'),
         'confirmada', 'venta', %(mc)s, %(canal)s, %(mc)s, %(mu)s, %(mo)s,
         %(descripcion)s, %(guia)s, %(paqueteria)s, %(fecha_venta)s, %(entrega_limite)s,
         %(moneda)s, %(total)s, %(comision)s, 'marketplace', %(q)s, %(nombre)s, 'automatico',
         %(clave)s, now(), %(q)s, %(nombre)s
    from fo
  returning id
), l as (
  insert into ventas.ov_lineas (orden_id, linea, sku, titulo, imagen, cantidad, precio_unitario,
                             almacen, reservado)
  select o.id, row_number() over (order by x.linea, x.sku), x.sku, x.titulo, x.imagen, x.n,
         x.precio_unitario, x.almacen, x.n
    from o, x
  returning id
), m as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select o.id, 'sistema', 'creada', %(cuerpo)s, %(datos)s::jsonb, %(q)s, %(nombre)s, 'automatico'
    from o
  returning id
)
select coalesce((select id from o), (select min(id) from previa)) as id,
       case when exists (select 1 from previa) then 'ya_existia' else 'creada' end as resultado,
       ops.exigir(exists (select 1 from previa)
                  or ((select count(*) from o) = 1
                      and (select count(*) from l) = (select count(*) from r)),
                  'no_alcanzo')
     + ops.exigir(exists (select 1 from previa) or (select count(*) from m) = 1,
                  'crear_auto_escrituras_no_cuadran') as cuadra
""")

_SQL_DIAGNOSTICO_AUTO = """
select r.sku::text as sku, r.n,
       coalesce(a.fuente = 'kubera' and a.admite_ov and a.surte_ventas, false) as surte,
       sa.sku is not null as con_saldo, sa.libre
  from jsonb_to_recordset(%(lineas)s::jsonb) as r(linea int, sku citext, n int)
  left join almacen.almacenes a on a.codigo = %(alm)s
  left join almacen.stock_almacen sa on sa.sku = r.sku and sa.almacen = %(alm)s
 order by r.linea
"""


def _venta_auto(venta: Any) -> dict[str, Any]:
    """El encabezado de una orden automática, validado. Acepta las llaves de una
    `VentaMarketplace` (canal, cuenta, orden, fecha) o las de la tabla (mp_*)."""
    if not isinstance(venta, dict):
        raise Invalido("La venta no es válida.")

    def uno(*llaves: str) -> Any:
        return next((venta[k] for k in llaves if venta.get(k) is not None), None)

    mc = (_texto(uno("mp_canal", "canal"), _TEXTOS["mp_canal"], "mp_canal") or "").lower()
    mu = (_texto(uno("mp_cuenta", "cuenta"), _TEXTOS["mp_cuenta"], "mp_cuenta") or "").upper()
    mo = _texto(uno("mp_orden", "orden"), _TEXTOS["mp_orden"], "mp_orden") or ""
    if not (mc and mu and mo):
        raise Invalido("Una orden automática necesita el canal, la cuenta y el número de la venta.")
    moneda = _limpio(venta.get("moneda")).upper() or "MXN"
    if not re.fullmatch(r"[A-Z]{3}", moneda):
        raise Invalido("La moneda son tres letras (MXN, USD…).")
    total, comision = venta.get("total"), venta.get("comision")
    return {"mc": mc, "mu": mu, "mo": mo, "canal": mc if mc in CANALES else "otro",
            "descripcion": _texto(venta.get("descripcion"), _TEXTOS["descripcion"], "descripcion"),
            "guia": _texto(venta.get("guia"), _TEXTOS["guia"], "guia"),
            "paqueteria": _texto(venta.get("paqueteria"), _TEXTOS["paqueteria"], "paqueteria"),
            "fecha_venta": _fecha(uno("fecha_venta", "fecha"), "fecha_venta"),
            "entrega_limite": _fecha(venta.get("entrega_limite"), "entrega_limite"),
            "moneda": moneda,
            "total": None if total is None else _dinero(total, "total"),
            "comision": Decimal("0.00") if comision is None else _dinero(comision, "comisión")}


def _lineas_auto(lineas: Any) -> list[dict[str, Any]]:
    """Los renglones de una venta, AGRUPADOS POR SKU antes de la sentencia: con un
    SKU repetido el `UPDATE … from x` aparta una sola vez y la comprobación final
    daría un `no_alcanzo` engañoso (guía §4.9 d). El mismo SKU a dos precios (la
    2.ª pieza al 50 %) queda en UN renglón con el precio promedio ponderado: es lo
    que el marketplace cobró, así que se conserva el dinero."""
    if not isinstance(lineas, (list, tuple)) or not lineas:
        raise Invalido("Una orden automática necesita al menos un renglón.")
    por_sku: dict[str, dict[str, Any]] = {}
    importes: dict[str, Decimal] = {}
    for i, c in enumerate(lineas, 1):
        donde = f"Renglón {i}"
        if not isinstance(c, dict):
            raise Invalido(f"{donde}: formato inválido.")
        sku = _limpio(c.get("sku"))
        if not 1 <= len(sku) <= 80:
            raise Invalido(f"{donde}: el SKU es obligatorio (1 a 80 caracteres).")
        n = _cantidad(c.get("cantidad") if c.get("cantidad") is not None else c.get("n"), donde)
        precio = c.get("precio_unitario")
        precio = (Decimal("0.00") if precio is None
                  else _dinero(precio, f"{donde}: precio unitario", MAX_PRECIO))
        k = sku.lower()
        if k in por_sku:
            por_sku[k]["n"] += n
            importes[k] += precio * n
            continue
        importes[k] = precio * n
        titulo = _limpio(c.get("titulo"))[:300] or None
        por_sku[k] = {"sku": sku, "n": n, "titulo": titulo, "imagen": _imagen(c.get("imagen"))}
    if len(por_sku) > MAX_RENGLONES:
        raise Invalido(f"Una orden admite hasta {MAX_RENGLONES} renglones.")
    salida = []
    for linea, (k, r) in enumerate(por_sku.items(), 1):
        if r["n"] > MAX_CANTIDAD:
            raise Invalido(f"{r['sku']} suma más de {MAX_CANTIDAD:,} piezas.")
        precio = (importes[k] / r["n"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        salida.append({"linea": linea, **r, "precio_unitario": precio})
    return salida


def _porque_no_alcanzo_auto(lineas: list[dict[str, Any]], almacen: str, cur: Any = None) -> str:
    try:
        diag = _filas(_SQL_DIAGNOSTICO_AUTO,
                      {"alm": almacen, "lineas": _json([{"linea": r["linea"], "sku": r["sku"],
                                                         "n": r["n"]} for r in lineas])}, cur)
    except Exception as exc:  # noqa: BLE001 — sin diagnóstico sale el texto general
        log.warning("ordenes_venta.crear_auto: no se pudo releer el saldo (%s)",
                    type(exc).__name__)
        return texto_de_motivo("no_alcanzo")
    if diag and not diag[0]["surte"]:
        return (f"La bodega {almacen} no surte ventas (o no admite órdenes de venta): "
                "no se apartó nada.")
    faltantes = [{"sku": d["sku"], "pide": int(d["n"]), "almacen": almacen,
                  "libre": int(d["libre"]) if d["con_saldo"] else None}
                 for d in diag if not d["con_saldo"] or int(d["libre"]) < int(d["n"])]
    return _texto_no_alcanzo(faltantes) if faltantes else texto_de_motivo("no_alcanzo")


def crear_auto(venta: dict[str, Any], lineas: list[dict[str, Any]], almacen: str,
               cur: Any = None) -> dict[str, Any]:
    """La orden AUTOMÁTICA de una venta de marketplace que sale de una bodega de
    kubera: nace ya `confirmada` y con sus renglones apartados, todo o nada.

    ⚠️ NADIE LA LLAMA TODAVÍA: su llamador es el planeador (plan v3 §5, fase B).
    Exige la bandera `ov_generacion_auto` (sin fila o sin poder leerla: apagada).

    `venta` trae la llave (canal, cuenta, orden) y TODO el contenido (total,
    comisión, moneda, fecha, descripción, guía, paquetería): lo que no venga
    aquí ya no se podrá anotar. `lineas` = `[{sku, cantidad, precio_unitario,
    titulo, imagen}]`. Devuelve `{resultado, orden, mensaje}`:

      creada      nació confirmada y apartada
      ya_existia  esa venta (o esa clave) ya tenía orden viva y APARTADA (o ya
                  entregada): ÉXITO, sin apartar de nuevo
      borrador_previo  esa venta ya tenía orden, pero es un BORRADOR que capturó
                  una persona y no confirmó: NO hay una sola pieza apartada. No
                  es éxito ni se toca el borrador: quien llama NO debe dar la
                  venta por cubierta (ni comprar la guía) hasta que alguien lo
                  confirme o lo cancele
      no_alcanzo  no hay libre suficiente, el SKU no tiene saldo, o la bodega no
                  surte ventas: no se creó nada. Antes de decirlo se RELEE la
                  orden viva de la venta (otra pasada pudo crearla y por eso ya
                  no alcanza): si existe, es `ya_existia` (o `borrador_previo`).

    Lo que sigue (la guía de Temu, la sale.order de la parte de Odoo) va DESPUÉS
    de que esta función regrese: primero el sistema propio, luego el externo."""
    _exigir_tablas(cur)
    if not generacion_auto(cur=cur):
        raise Apagado(_MSG_AUTO_APAGADA)
    v = _venta_auto(venta)
    renglones = _lineas_auto(lineas)
    alm = _limpio(almacen).upper()
    if not re.fullmatch(r"[A-Z0-9]{2,12}", alm):
        raise Invalido("Falta la bodega de kubera de la que sale la venta.")
    if v["total"] is None:
        v["total"] = _total_que_cabe(
            sum((r["precio_unitario"] * r["n"] for r in renglones), Decimal("0.00")))
    firma = _firma(AUTOMATICO)
    piezas = sum(r["n"] for r in renglones)
    clave = f"mp:{v['mc']}:{v['mu']}:{v['mo']}"
    params = {**v, "q": firma["q"], "nombre": firma["nombre"], "alm": alm, "clave": clave,
              "lineas": _json([{**r, "precio_unitario": str(r["precio_unitario"])}
                               for r in renglones]),
              "cuerpo": (f"Creada y apartada por la venta {v['mc']} {v['mo']} · "
                         f"{_renglones(len(renglones))}, {_piezas(piezas)} en {alm}"),
              "datos": _json({"op": _op(), "mp": {"canal": v["mc"], "cuenta": v["mu"],
                                                  "orden": v["mo"]},
                              "total": float(v["total"]),
                              "lineas": [{"sku": r["sku"], "titulo": r["titulo"],
                                          "cantidad": r["n"], "almacen": alm, "reservado": r["n"],
                                          "precio_unitario": float(r["precio_unitario"])}
                                         for r in renglones]})}

    def fin(resultado: str, orden_id: int, mensaje: str) -> dict[str, Any]:
        orden = _orden(_leer_id(orden_id, cur), AUTOMATICO, cur)
        # La base no distingue (para `previa` un borrador ligado a la venta es
        # una orden viva), pero quien llama sí tiene que distinguirlo: un
        # borrador no aparta nada, y contestar `ya_existia` —que es ÉXITO— era
        # decirle «esta venta ya está cubierta» sin una pieza apartada.
        if resultado == "ya_existia" and orden.get("estado") == "borrador":
            return {"resultado": "borrador_previo", "orden": orden,
                    "mensaje": (f"Esa venta ya tiene el borrador {orden.get('folio')}, sin "
                                "confirmar: no hay nada apartado.")}
        return {"resultado": resultado, "mensaje": mensaje, "orden": orden}

    fila = None
    for intento in (1, 2):
        try:
            fila = _transicion("crear_auto", SQL_CREAR_AUTO, params, cur)
            break
        except _Rechazo as r:
            # 23505 de la clave o de la venta: otra pasada la creó entre mi foto y
            # mi INSERT. Basta volver a correr la MISMA sentencia: con la foto
            # nueva, `previa` la ve y devuelve `ya_existia`.
            if r.clase == "ya_existia" and intento == 1:
                continue
            if r.clase == "negocio" and r.detalle == "no_alcanzo":
                viva = _venta_ligada(v["mc"], v["mu"], v["mo"], cur=cur)
                if viva:
                    return fin("ya_existia", viva["id"],
                               f"Esa venta ya tenía la orden {viva['folio']}.")
                return {"resultado": "no_alcanzo", "orden": None,
                        "mensaje": _porque_no_alcanzo_auto(renglones, alm, cur)}
            raise _error_de_rechazo(r, "crear_auto") from r.exc
    if not fila or not fila.get("id"):
        raise ErrorOV(_MSG_INESPERADO, status=502)
    if fila["resultado"] == "ya_existia":
        return fin("ya_existia", fila["id"], "Esa venta ya tenía su orden de venta.")
    return fin("creada", fila["id"], "Orden creada y apartada.")


# ══════════════════════════════════════════════════════════════════════════════
# Chat (`ventas.ov_mensajes` es de SÓLO AGREGAR)
# ══════════════════════════════════════════════════════════════════════════════

# `datos.op` y `datos.clave` son fontanería (las marcas de idempotencia): no
# salen por la API.
_MENSAJE_JSON = """json_build_object(
        'id', m.id, 'orden_id', m.orden_id, 'tipo', m.tipo, 'evento', m.evento,
        'cuerpo', m.cuerpo, 'datos', nullif(m.datos - 'op' - 'clave', '{}'::jsonb), 'autor', m.autor,
        'autor_nombre', m.autor_nombre, 'via', m.via, 'creado_at', m.creado_at)"""

# Con `.replace` y no `.format`: el SQL lleva un '{}' literal (el jsonb vacío).
_SQL_MENSAJES = """
select o.rev, o.estado,
       (select count(*) from ventas.ov_mensajes m where m.orden_id = o.id) as total,
       (select coalesce(max(m.id), 0) from ventas.ov_mensajes m where m.orden_id = o.id) as ultimo_id,
       coalesce((select json_agg(""" + _MENSAJE_JSON + """ order by m.id)
                   from ventas.ov_mensajes m
                  where m.orden_id = o.id and __FILTRO__), '[]'::json) as mensajes
  from ventas.ov_ordenes o
 where o.id = %(id)s
"""

# Un mensaje de una PERSONA: `tipo = 'usuario'`, sin `evento`. `not exists` por
# la marca (`op`: la repetición de un reintento transitorio no deja el mismo
# mensaje dos veces) y por la `clave` (el reenvío de la pantalla tampoco).
SQL_MENSAJE_USUARIO = _una("""
insert into ventas.ov_mensajes (orden_id, tipo, cuerpo, datos, autor, autor_nombre, via)
select o.id, 'usuario', %(cuerpo)s,
       jsonb_strip_nulls(jsonb_build_object('op', %(op)s::text, 'clave', %(clave)s::text)),
       %(q)s, %(nombre)s, %(via)s
  from ventas.ov_ordenes o
 where o.id = %(id)s and o.borrada_at is null
   and not exists (select 1 from ventas.ov_mensajes y
                    where y.orden_id = o.id
                      and (y.datos->>'op' = %(op)s
                           or (%(clave)s::text is not null and y.tipo = 'usuario'
                               and y.autor = %(q)s and y.datos->>'clave' = %(clave)s::text)))
returning id
""")


def _resp_mensajes(orden_id: Any, filtro: str, params: dict[str, Any],
                   cur: Any = None) -> dict[str, Any]:
    n = _id(orden_id)
    fila = _fila(_SQL_MENSAJES.replace("__FILTRO__", filtro), {**params, "id": n}, cur)
    if not fila:
        raise NoExiste(f"No existe la orden de venta {orden_id}.")
    return {"mensajes": list(fila["mensajes"] or []), "ultimo_id": int(fila["ultimo_id"] or 0),
            "total": int(fila["total"] or 0), "rev": int(fila["rev"]), "estado": fila["estado"]}


def mensajes(orden_id: int, desde_id: int = 0, cur: Any = None) -> dict[str, Any]:
    """Los mensajes con `id > desde_id`, más `total`, `rev` y `estado` ACTUALES:
    si `total` no cuadra con lo que el cliente tiene, recarga todo; si `rev`
    cambió, alguien movió la orden y relee el documento."""
    try:
        desde = max(0, int(desde_id or 0))
    except (TypeError, ValueError):
        desde = 0
    _si_caida(cur)        # el chat se sondea solo: con kubera caída, ni se intenta
    _exigir_tablas(cur)
    return _resp_mensajes(orden_id, "m.id > %(desde)s", {"desde": desde}, cur)


def enviar_mensaje(orden_id: int, quien: Quien, cuerpo: str, clave: str | None = None,
                   cur: Any = None) -> dict[str, Any]:
    """Un mensaje de una persona. Devuelve SÓLO el mensaje nuevo en `mensajes`.
    No mueve `rev`: el chat no cambia el documento.

    IDEMPOTENTE POR `clave` (la genera el navegador, una por mensaje escrito; se
    guarda en `datos.clave`). El caso que cubre: el mensaje SÍ se guardó pero la
    respuesta se perdió; la persona vuelve a dar Enter y, con la misma clave, se
    devuelve el que ya estaba. La clave vale por orden y por AUTOR. (Sin índice
    único detrás: dos envíos SIMULTÁNEOS con la misma clave podrían colarse los
    dos; el reenvío, que es el caso real, no.)"""
    texto = _limpio(cuerpo)
    if not texto:
        raise Invalido("El mensaje está vacío.")
    if len(texto) > MAX_MENSAJE:
        raise Invalido(f"El mensaje admite hasta {MAX_MENSAJE} caracteres.")
    clave_ = _limpio(clave) or None
    if clave_ and len(clave_) > MAX_CLAVE:
        raise Invalido(f"La clave del mensaje admite hasta {MAX_CLAVE} caracteres.")
    _exigir_tablas(cur)
    o = _leer_cabeza(orden_id, cur)
    m = _motivo("mensajes", o, quien, True)
    if m:
        raise _error_de(m)
    firma = _firma(quien)
    op = _op()
    try:
        _transicion("enviar_mensaje", SQL_MENSAJE_USUARIO,
                    {**firma, "id": o["id"], "cuerpo": texto, "op": op, "clave": clave_}, cur)
    except _Rechazo as r:
        raise _error_de_rechazo(r, "enviar_mensaje") from r.exc
    if clave_:
        resp = _resp_mensajes(
            o["id"], "m.tipo = 'usuario' and m.autor = %(autor)s "
                     "and m.datos->>'clave' = %(clave)s",
            {"autor": firma["q"], "clave": clave_}, cur)
        resp["mensajes"] = resp["mensajes"][:1]        # el primero es el que quedó
    else:
        resp = _resp_mensajes(o["id"], "m.datos->>'op' = %(op)s", {"op": op}, cur)
    if not resp["mensajes"]:
        raise Conflicto("La orden se borró mientras tanto; el mensaje no se guardó.")
    return resp


# ══════════════════════════════════════════════════════════════════════════════
# PDF (sólo si existe el bucket `ordenes-venta`)
# ══════════════════════════════════════════════════════════════════════════════

# EL ORDEN CON STORAGE ES FIJO (no comparten transacción; 0064 §8, guía §3.7):
#   · subir el objeto y DESPUÉS insertar la fila;
#   · marcar `borrado_at` y DESPUÉS borrar el objeto.
# Así lo único que puede quedar suelto es un objeto huérfano, nunca una fila que
# apunta a nada. Y la red va siempre FUERA de la transacción.
#
# Adjuntar o quitar SUBE `rev` sin exigirla: es lo que hace que la pantalla de
# los demás relea el documento. Fuera de borrador `rev` sí puede cambiar (está
# en la lista de lo permitido). El aviso en el chat es `tipo = 'sistema'` con
# `evento` NULL: «PDF adjunto» no está en el catálogo cerrado de eventos.
SQL_ARCHIVO_ALTA = _una("""
with o as (
  update ventas.ov_ordenes v set rev = v.rev + 1
   where v.id = %(id)s and v.borrada_at is null
  returning v.id
), a as (
  insert into ventas.ov_archivos (orden_id, tipo, nombre, ruta, sha256, bytes, subido_por,
                               subido_nombre)
  select o.id, %(tipo)s, %(archivo)s, %(ruta)s, %(sha)s, %(bytes)s, %(q)s, %(nombre)s
    from o
  returning id
), m as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select o.id, 'sistema', null, %(cuerpo)s, %(datos)s::jsonb, %(q)s, %(nombre)s, %(via)s
    from o
  returning id
)
select (select id from a) as id,
       ops.exigir((select count(*) from o) = 1, 'ov_borrada_o_no_existe')
     + ops.exigir((select count(*) from a) = 1 and (select count(*) from m) = 1,
                  'archivo_escrituras_no_cuadran') as cuadra
""")

# La fila de un archivo sólo cambia para marcar `borrado_at` y `borrado_por`,
# una vez (ov_archivos_inmutable); no se borra. Primero la fila de la ORDEN, como
# en toda sentencia (guía §4.3): al revés —el archivo y después la orden— se
# cruzaría con un alta del MISMO PDF, que toma la orden y espera al archivo.
SQL_ARCHIVO_BAJA = _una("""
with o as (
  update ventas.ov_ordenes v set rev = v.rev + 1
   where v.id = %(id)s and v.borrada_at is null
     and exists (select 1 from ventas.ov_archivos x
                  where x.id = %(aid)s and x.orden_id = v.id and x.borrado_at is null)
  returning v.id
), a as (
  update ventas.ov_archivos x set borrado_at = now(), borrado_por = %(q)s
    from o
   where x.id = %(aid)s and x.orden_id = o.id and x.borrado_at is null
  returning x.id, x.orden_id, x.nombre
), m as (
  insert into ventas.ov_mensajes (orden_id, tipo, evento, cuerpo, datos, autor, autor_nombre, via)
  select a.orden_id, 'sistema', null, 'PDF quitado: ' || a.nombre,
         jsonb_build_object('archivo_id', a.id, 'nombre', a.nombre), %(q)s, %(nombre)s, %(via)s
    from a
  returning id
)
select (select count(*) from a) as quitados,
       ops.exigir((select count(*) from a) = 1, 'archivo_ya_no_esta')
     + ops.exigir((select count(*) from o) = 1 and (select count(*) from m) = 1,
                  'archivo_escrituras_no_cuadran') as cuadra
""")


def _tipo_archivo(tipo: Any) -> str:
    t = _limpio(tipo).lower()
    if t not in TIPOS_ARCHIVO:
        raise Invalido("Indica qué es el PDF: comprobante, factura o envío a FULL (envio_full). "
                       "Las guías con la dirección del comprador NO se guardan aquí.")
    return t


def _indice_vivo(orden_id: int, sha: str, cur: Any = None) -> bool:
    """¿Hay una fila VIVA del índice para ese PDF en esa orden?"""
    return bool(_fila("""select 1 as hay from ventas.ov_archivos
                          where orden_id = %(id)s and sha256 = %(sha)s and borrado_at is null
                          limit 1""", {"id": orden_id, "sha": sha}, cur))


def _quitar_huerfano(orden_id: int, sha: str, ruta: str, subido: bool, cur: Any = None) -> None:
    """Borra del bucket el objeto que ESTA llamada subió y que se quedó sin
    índice. Nunca lanza. Antes de borrar se relee el índice, y sin poder leerlo
    NO se borra: el COMMIT pudo entrar aunque la conexión muriera al contestar,
    y dos subidas simultáneas del mismo PDF comparten el objeto. En la duda se
    deja (un objeto huérfano está aceptado por diseño; una fila que apunta a
    nada, no)."""
    if not subido:
        return
    try:
        if _indice_vivo(orden_id, sha, cur):
            return
    except Exception as exc:  # noqa: BLE001 — sin saber, no se borra
        log.warning("ordenes_venta: %s quedó en el bucket sin poder comprobar su índice (%s)",
                    ruta, type(exc).__name__)
        return
    try:
        ov_storage.borrar(ruta)
        log.warning("ordenes_venta: se quitó del bucket %s (se subió pero no quedó en el "
                    "índice de la orden %s)", ruta, orden_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("ordenes_venta: PDF HUÉRFANO en el bucket: %s (orden %s) no se pudo "
                    "quitar: %s", ruta, orden_id, exc)


def subir_archivo(orden_id: int, quien: Quien, tipo: str, nombre: str, datos: bytes,
                  cur: Any = None) -> dict[str, Any]:
    """Adjunta un PDF (`tipo`: comprobante | factura | envio_full, obligatorio).
    Las guías con la dirección del comprador NO se guardan en kubera (D5).

    Sólo si existe el bucket `ordenes-venta`: si no, 409 que lo dice. Primero se
    sube el objeto (FUERA de la transacción) y después UNA sentencia inserta el
    índice, sube `rev` y deja el aviso. El mismo PDF dos veces no duplica."""
    firma = _firma(quien)
    tipo_ = _tipo_archivo(tipo)
    _exigir_tablas(cur)
    o = _leer_id(orden_id, cur)
    m = _motivo("subir_archivo", o, quien, True, hay_bucket(cur=cur))
    if m:
        raise _error_de(m)
    if not isinstance(datos, (bytes, bytearray)) or not datos:
        raise Invalido("El archivo está vacío.")
    datos = bytes(datos)
    if len(datos) > MAX_PDF:
        raise Grande(f"El PDF pesa {_peso(len(datos))}; el tope es {MAX_PDF // 1048576} MB.")
    if not datos.startswith(b"%PDF"):
        raise Invalido("El archivo no es un PDF.")
    nombre_ = _nombre_pdf(nombre)
    sha = hashlib.sha256(datos).hexdigest()
    if any(a.get("sha256") == sha for a in (o.get("archivos") or [])):
        return {"ok": True, "orden": _orden(o, quien, cur), "mensaje": "Ese PDF ya estaba adjunto."}
    ruta = f"{o['folio']}/{sha}.pdf"
    try:
        # «existe» = ese mismo contenido ya estaba en el bucket (otra subida): si
        # después hay que limpiar, ESE objeto no es de esta llamada y no se toca.
        subido = ov_storage.subir(ruta, datos) == "subido"
    except Exception as exc:  # noqa: BLE001 — Storage caído no es un 500 nuestro
        log.warning("ordenes_venta: no se pudo subir %s: %s", ruta, exc)
        raise FallaStorage("No se pudo guardar el PDF en Storage; intenta de nuevo.") from exc
    params = {**firma, "id": o["id"], "tipo": tipo_, "archivo": nombre_, "ruta": ruta, "sha": sha,
              "bytes": len(datos), "cuerpo": f"PDF adjunto: {nombre_} ({_peso(len(datos))})",
              "datos": _json({"nombre": nombre_, "tipo": tipo_, "bytes": len(datos),
                              "sha256": sha})}
    try:
        _transicion("subir_archivo", SQL_ARCHIVO_ALTA, params, cur)
    except _Rechazo as r:
        if r.clase == "ya_existia":
            # ov_archivos_vivo_uq: ese PDF ya está en la orden (otra subida igual
            # ganó la carrera). El objeto es el mismo (la ruta sale del sha256).
            return _resp(o["id"], quien, "Ese PDF ya estaba adjunto.", cur)
        _quitar_huerfano(o["id"], sha, ruta, subido, cur)
        if r.clase == "negocio":
            raise Conflicto("La orden se borró mientras tanto; el PDF no se adjuntó.") from r.exc
        raise _error_de_rechazo(r, "subir_archivo") from r.exc
    except Exception:
        _quitar_huerfano(o["id"], sha, ruta, subido, cur)
        raise
    return _resp(o["id"], quien, f"PDF adjunto: {nombre_}.", cur)


def _archivo(orden_id: Any, archivo_id: Any, cur: Any = None) -> dict[str, Any]:
    try:
        ids = {"id": int(orden_id), "aid": int(archivo_id)}
    except (TypeError, ValueError):
        raise NoExiste("No existe ese PDF.") from None
    fila = _fila("""select id, orden_id, nombre, ruta, sha256, bytes from ventas.ov_archivos
                     where id = %(aid)s and orden_id = %(id)s and borrado_at is null""", ids, cur)
    if not fila:
        raise NoExiste("No existe ese PDF (o ya se quitó).")
    return fila


def bajar_archivo(orden_id: int, archivo_id: int, quien: Quien,
                  cur: Any = None) -> tuple[str, bytes]:
    """(nombre, pdf). Sale por el backend con sesión: jamás una URL pública ni
    firmada. Exige `operador` o `admin` (y de una orden BORRADA, sólo admin): el
    piso del RBAC para este GET es `lectura`."""
    if not quien.escribe:                    # el rol no depende de la orden: ni se lee
        raise _error_de(_no_escribe(quien))
    _exigir_tablas(cur)
    o = _leer_cabeza(orden_id, cur)
    m = _motivo("bajar_archivo", o, quien, True)
    if m:
        raise _error_de(m)
    a = _archivo(orden_id, archivo_id, cur)
    try:
        datos = ov_storage.bajar(a["ruta"])
    except Exception as exc:  # noqa: BLE001
        log.warning("ordenes_venta: no se pudo bajar %s: %s", a["ruta"], exc)
        raise FallaStorage("No se pudo leer el PDF de Storage; intenta de nuevo.") from exc
    # Un objeto que no cuadra con su huella no es el que se adjuntó.
    if hashlib.sha256(datos).hexdigest() != a["sha256"]:
        raise FallaStorage("El PDF guardado no cuadra con su huella; no se entrega.")
    return a["nombre"], datos


def borrar_archivo(orden_id: int, archivo_id: int, quien: Quien,
                   cur: Any = None) -> dict[str, Any]:
    """Admin: quita un PDF. PRIMERO se marca en el índice (`borrado_at`) y
    DESPUÉS se borra el objeto: si Storage falla, queda un objeto huérfano que
    nadie ve —y un aviso en el log con su ruta—, nunca una fila viva sin archivo.

    Quitar es idempotente: si entre la lectura y la marca OTRO lo quitó (o es mi
    propia marca, repetida por el reintento transitorio), el final es el que se
    pidió —el PDF ya no está—, y el objeto se borra igual."""
    firma = _firma(quien)
    _exigir_tablas(cur)
    o = _leer_cabeza(orden_id, cur)
    m = _motivo("borrar_archivo", o, quien, True)
    if m:
        raise _error_de(m)
    a = _archivo(orden_id, archivo_id, cur)
    try:
        _transicion("borrar_archivo", SQL_ARCHIVO_BAJA,
                    {**firma, "id": a["orden_id"], "aid": a["id"]}, cur)
    except _Rechazo as r:
        if r.clase != "negocio":
            raise _error_de_rechazo(r, "borrar_archivo") from r.exc
        # 0 filas: ya no estaba vivo. Se RELEE: si quedó marcado, sigue (el objeto
        # todavía puede estar en el bucket); si la fila no está marcada, lo que
        # cambió fue la orden (se borró) y no se toca nada.
        marcado = _fila("select borrado_at is not null as ya from ventas.ov_archivos "
                        "where id = %(aid)s and orden_id = %(id)s",
                        {"id": a["orden_id"], "aid": a["id"]}, cur)
        if not (marcado and marcado.get("ya")):
            raise NoExiste("No existe ese PDF (o ya se quitó).") from r.exc
    try:
        ov_storage.borrar(a["ruta"])
    except Exception as exc:  # noqa: BLE001 — la fila ya está marcada: no se deshace
        log.warning("ordenes_venta: PDF HUÉRFANO en el bucket: %s (orden %s) ya no está en el "
                    "índice pero no se pudo borrar de Storage: %s", a["ruta"], o["id"], exc)
    return _resp(o["id"], quien, f"PDF quitado: {a['nombre']}.", cur)


# ══════════════════════════════════════════════════════════════════════════════
# Lista y estado del módulo
# ══════════════════════════════════════════════════════════════════════════════

# «Por devolver» = se espera devolución y no ha llegado, sea cual sea el estado:
# la 0064 admite `devolucion_estado` en entregada y en entregada_cancelada.
_SQL_CONTEOS = """
select count(*) filter (where borrada_at is null) as todas,
       count(*) filter (where borrada_at is null and estado = 'borrador') as borrador,
       count(*) filter (where borrada_at is null and estado = 'confirmada') as confirmada,
       count(*) filter (where borrada_at is null and estado = 'entregada') as entregada,
       count(*) filter (where borrada_at is null and estado = 'cancelada') as cancelada,
       count(*) filter (where borrada_at is null and estado = 'entregada_cancelada')
           as entregada_cancelada,
       count(*) filter (where borrada_at is null and devolucion_estado = 'pendiente')
           as por_devolver,
       count(*) filter (where borrada_at is not null) as borradas
  from ventas.ov_ordenes
"""


def _entero(v: Any, omision: int, minimo: int, maximo: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = omision
    return max(minimo, min(maximo, n))


def _lista_vacia(por_pagina: int, motivo: str) -> dict[str, Any]:
    return {"ok": False, "falta_migracion": True, "motivo": motivo, "ordenes": [],
            "total": 0, "pagina": 1, "por_pagina": por_pagina, "paginas": 1,
            "conteos": dict.fromkeys(FILTROS, 0)}


def listar(estado: str | None = None, q: str | None = None, canal: str | None = None,
           pagina: int = 1, por_pagina: int = 40, cur: Any = None) -> dict[str, Any]:
    """La lista paginada (más recientes primero) y los conteos de TODA la tabla.
    Por omisión EXCLUYE las borradas. Sin las migraciones no truena: lo dice."""
    por_pagina = _entero(por_pagina, 40, 1, 200)
    pagina = _entero(pagina, 1, 1, 1_000_000)
    filtro = _limpio(estado).lower() or "todas"
    if filtro not in FILTROS:
        raise Invalido(f"Filtro de estado desconocido: {filtro}.")
    if filtro == "borradas":
        donde = ["o.borrada_at is not null"]
    else:
        donde = ["o.borrada_at is null"]
        if filtro == "por_devolver":
            donde.append("o.devolucion_estado = 'pendiente'")
        elif filtro != "todas":
            donde.append("o.estado = %(estado)s")
    params: dict[str, Any] = {"estado": filtro, "lim": por_pagina,
                              "off": (pagina - 1) * por_pagina}
    canal_ = _limpio(canal).lower()
    if canal_:
        donde.append("(lower(o.canal) = %(canal)s or lower(o.mp_canal) = %(canal)s)")
        params["canal"] = canal_
    busca = _limpio(q)[:100]
    if busca:
        donde.append(
            "(o.folio ilike %(q)s or o.mp_orden ilike %(q)s or o.cliente ilike %(q)s "
            "or o.descripcion ilike %(q)s or o.guia ilike %(q)s "
            "or exists (select 1 from ventas.ov_lineas l "
            "            where l.orden_id = o.id and l.sku::text ilike %(q)s))")
        params["q"] = f"%{_escapar_like(busca)}%"
    w = " and ".join(donde)

    def _leer(c: Any) -> tuple[dict[str, Any], int, list[dict[str, Any]]]:
        c.execute(_SQL_CONTEOS)
        conteos = _dicts(c)[0]
        c.execute(f"select count(*) as n from ventas.ov_ordenes o where {w}", params)
        total = int(_dicts(c)[0]["n"])
        c.execute("select" + _COLS + _DESDE + f" where {w} "
                  "order by o.creado_at desc, o.id desc limit %(lim)s offset %(off)s", params)
        return conteos, total, _dicts(c)

    _si_caida(cur)        # la lista se sondea sola: con kubera caída, ni se intenta
    try:
        _exigir_tablas(cur)
        conteos, total, filas = _tx(_leer, cur)
    except FaltaMigracion as exc:
        return _lista_vacia(por_pagina, str(exc))
    return {"ok": True, "ordenes": [_resumen(f) for f in filas], "total": total,
            "pagina": pagina, "por_pagina": por_pagina,
            "paginas": max(1, math.ceil(total / por_pagina)),
            "conteos": {k: int(conteos.get(k) or 0) for k in FILTROS}}


def estado_modulo(quien: Quien, cur: Any = None) -> dict[str, Any]:
    """`EstadoModulo` de tipos.ts: quién soy para el módulo, qué está encendido
    (las banderas, de SÓLO LECTURA), el catálogo de bodegas y si se pueden
    adjuntar PDF. Nunca lanza: lo que no se pudo leer se dice en `motivo`."""
    e: dict[str, Any] = {
        "ok": True, "falta_migracion": False, "habilitado": False,
        "banderas": {BANDERA_MODULO: dict(_BANDERA_APAGADA),
                     BANDERA_AUTO: dict(_BANDERA_APAGADA)},
        "bodegas": [], "archivos": {"disponible": False, "motivo": _MSG_SIN_BUCKET},
        "yo": {"actor": quien.actor, "nombre": quien.nombre, "rol": quien.rol,
               "via": quien.via if quien.via in VIAS else "panel",
               "admin": quien.admin, "escribe": quien.escribe}}
    try:
        if cur is None and en_pausa():      # /estado se sondea solo: en pausa, ni se intenta
            e.update(ok=False, motivo=_MSG_SIN_BASE)
            return e
        e["banderas"] = {BANDERA_MODULO: estado_bandera(BANDERA_MODULO, cur=cur),
                         BANDERA_AUTO: estado_bandera(BANDERA_AUTO, cur=cur)}
        e["habilitado"] = bool(e["banderas"][BANDERA_MODULO]["encendido"])
        t = _tablas(cur=cur)
        if t is None:
            e.update(ok=False, motivo="No se pudo leer kubera; intenta de nuevo.")
            return e
        if t is False:
            e.update(ok=False, falta_migracion=True, motivo=_MSG_MIGRACION)
            return e
        e["bodegas"] = bodegas(cur)
        if hay_bucket(cur=cur):
            e["archivos"] = {"disponible": True, "motivo": None}
    except SinBase as exc:              # ya quedó su línea en el log (una por minuto)
        e.update(ok=False, motivo=str(exc))
    except FaltaMigracion as exc:
        e.update(ok=False, falta_migracion=True, motivo=str(exc))
    except Exception as exc:  # noqa: BLE001 — «nunca lanza» es el contrato
        # El texto del error NO sale: un fallo de psycopg2 trae el host del
        # pooler y el usuario, y este motivo lo recibe cualquier sesión.
        log.warning("ordenes_venta.estado_modulo: %s", type(exc).__name__)
        e.update(ok=False, motivo="No se pudo leer kubera; intenta de nuevo.")
    return e


# ══════════════════════════════════════════════════════════════════════════════
# Nombres visibles y buscador de productos
# ══════════════════════════════════════════════════════════════════════════════

_NOMBRES: dict[str, tuple[float, str | None]] = {}
_NOMBRES_CANDADO = threading.Lock()
_NOMBRES_TTL = 300.0


def nombre_de(correo: str) -> str | None:
    """El nombre visible de un correo (core.usuarios), con caché de 5 minutos.
    Nunca lanza: sin nombre, la pantalla enseña el correo."""
    try:
        llave = (correo or "").strip().lower()
        if "@" not in llave:
            return None
        ahora = time.monotonic()
        with _NOMBRES_CANDADO:
            visto = _NOMBRES.get(llave)
        if visto and ahora - visto[0] < _NOMBRES_TTL:
            return visto[1]
        if en_pausa():
            # kubera no contesta: no se gastan otros 10 s por petición en un
            # nombre. Vale el último que se supo, aunque ya esté vencido.
            return visto[1] if visto else None
        fila = sdb.fetch_one("select nombre from core.usuarios where email = %s limit 1",
                             (llave,))
        nombre = (str((fila or {}).get("nombre") or "").strip() or None)
        with _NOMBRES_CANDADO:
            _NOMBRES[llave] = (ahora, nombre)
        return nombre
    except Exception as exc:  # noqa: BLE001
        if es_caida(exc):
            anotar_caida(exc, "nombre visible")
        else:
            log.debug("ordenes_venta.nombre_de(%s): %s", correo, exc)
        return None


# Las existencias salen de `almacen.stock_almacen`: una por bodega de kubera donde
# el SKU tiene fila de saldo. Sin fila no hay renglón («no lo sabemos»), que no
# es lo mismo que un cero.
_SQL_BUSCAR_SKUS = """
select p.sku::text as sku, p.name as nombre,
       coalesce((select json_agg(json_build_object(
                    'almacen', sa.almacen, 'fisico', sa.fisico, 'apartado', sa.apartado,
                    'libre', sa.libre) order by sa.almacen)
                   from almacen.stock_almacen sa where sa.sku = p.sku), '[]'::json) as existencias
  from core.products p
 where p.sku::text ilike %(parte)s or p.name ilike %(parte)s
 order by (p.sku::text ilike %(inicio)s) desc, (p.name ilike %(inicio)s) desc, p.sku::text
 limit %(lim)s
"""


def buscar_skus(q: str, limite: int = 20, cur: Any = None) -> dict[str, Any]:
    """El buscador de productos del documento. SÓLO kubera (el catálogo y el
    saldo de sus bodegas): nunca le pregunta a Odoo."""
    busca = _limpio(q)[:80]
    if len(busca) < 2:
        return {"opciones": []}
    _exigir_tablas(cur)
    esc = _escapar_like(busca)
    filas = _filas(_SQL_BUSCAR_SKUS, {"parte": f"%{esc}%", "inicio": f"{esc}%",
                                      "lim": _entero(limite, 20, 1, 50)}, cur)
    return {"opciones": [{"sku": f["sku"], "nombre": f["nombre"],
                          "existencias": list(f["existencias"] or [])} for f in filas]}


# ══════════════════════════════════════════════════════════════════════════════
# Ventas de marketplace (para prellenar un borrador a mano). SÓLO LECTURA.
# ══════════════════════════════════════════════════════════════════════════════

# Sólo estas columnas: channel.orders no trae datos del comprador y de aquí
# tampoco sale ninguno.
_SQL_VENTAS = """
select c.canal, c.cuenta, c.external_order_id as orden, c.creado_at as fecha,
       c.estado_canal, c.estado_wc, c.total, c.comision, c.es_fulfillment,
       coalesce((select json_agg(json_build_object(
                    'sku', i.sku::text, 'titulo', i.titulo, 'cantidad', i.cantidad,
                    'precio_unitario', i.precio_unitario, 'es_fulfillment', i.es_fulfillment)
                    order by i.linea)
                   from channel.order_items i
                  where i.canal = c.canal and i.cuenta = c.cuenta
                    and i.external_order_id = c.external_order_id), '[]'::json) as items
  from channel.orders c
"""

_SQL_AUTOMATIZACION = """
select o.canal, o.cuenta, o.external_order_id as orden, o.accion, o.guia, o.paqueteria,
       o.total, o.creado_at as fecha,
       coalesce((select json_agg(json_build_object(
                    'sku', i.sku::text, 'titulo', i.titulo, 'imagen', i.imagen,
                    'cantidad', i.cantidad, 'precio_unitario', i.precio_unitario)
                    order by i.linea)
                   from ops.odoo_sale_order_items i
                  where i.canal = o.canal and i.cuenta = o.cuenta
                    and i.external_order_id = o.external_order_id), '[]'::json) as items
  from ops.odoo_sale_orders o
 where o.external_order_id = any(%(ordenes)s)
"""

_MSG_SIN_VENTAS = "Esta base no tiene las tablas de ventas del canal."


def _llave(canal: Any, cuenta: Any, orden: Any) -> tuple[str, str, str]:
    return (str(canal or "").lower(), str(cuenta or "").lower(), str(orden or ""))


def _automatizacion(ordenes: list[str],
                    cur: Any = None) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Lo que la bitácora de Automatización (ops.odoo_sale_orders) sabe de esas
    ventas: guía, paquetería, foto por SKU y si el canal la canceló. SÓLO LEE.
    Esas tablas pueden no existir en un ambiente: sin ellas la venta sale sin
    esos datos, no truena. Pero «no existe» y «no se pudo leer» no son lo mismo:
    lo segundo deja un WARNING (con Temu, que sólo avisa por esta bitácora, es
    leer como viva una venta cancelada)."""
    if not ordenes:
        return {}
    try:
        return {_llave(f["canal"], f["cuenta"], f["orden"]): f
                for f in _filas(_SQL_AUTOMATIZACION, {"ordenes": list(ordenes)}, cur)}
    except FaltaMigracion:                    # 42P01/42703: el ambiente no la tiene
        log.info("ordenes_venta: este ambiente no tiene la bitácora de Automatización")
        return {}
    except Exception as exc:  # noqa: BLE001
        log.warning("ordenes_venta: NO se pudo leer la bitácora de Automatización (%s); las "
                    "ventas salen sin guía y SIN su marca de cancelación", type(exc).__name__)
        return {}


def _ligadas(ordenes: list[str], cur: Any = None) -> list[dict[str, Any]]:
    """Las órdenes propias VIVAS de esas ventas."""
    if not ordenes:
        return []
    return _filas("""select id, folio, estado, mp_canal, mp_cuenta, mp_orden
                       from ventas.ov_ordenes
                      where mp_orden = any(%(ordenes)s) and borrada_at is null
                        and estado <> 'cancelada'
                      order by id""", {"ordenes": list(ordenes)}, cur)


def cancelada_en_canal(canal: Any, estado_canal: Any, estado_wc: Any, accion: Any = None) -> bool:
    """¿El marketplace canceló esa venta? Temu NO actualiza channel.orders al
    cancelar: por eso se mira también la bitácora de Automatización."""
    ec = str(estado_canal or "").strip().lower()
    return (str(estado_wc or "").strip().lower() == "cancelled"
            or ec in ("cancelled", "canceled")
            or (str(canal or "").lower() == "temu" and ec == "3")
            or (accion or "") in ACCIONES_CANCELADA_CANAL)


def _lineas_venta(items: list[dict[str, Any]],
                  respaldo: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    """(renglones con SKU ya sumados, piezas de TODA la venta, renglones sin SKU).
    La foto y el precio que falte salen de la bitácora de Automatización.

    El mismo SKU en dos renglones de la venta (la 2.ª pieza al 50 %) queda en UNA
    línea con el precio PROMEDIO PONDERADO: aquí no hay a quién preguntarle —es
    lo que el marketplace cobró—, así que se conserva el dinero y se reparte
    entre las piezas (a 2 decimales)."""
    extra = {str(i.get("sku") or "").lower(): i for i in respaldo if i.get("sku")}
    por_sku: dict[str, dict[str, Any]] = {}
    importes: dict[str, Decimal] = {}
    piezas = sin_sku = 0
    for i in items:
        cantidad = int(i.get("cantidad") or 0)
        piezas += max(0, cantidad)
        sku = _limpio(i.get("sku"))
        if not sku or cantidad < 1:
            sin_sku += 0 if sku else 1
            continue
        k = sku.lower()
        e = extra.get(k, {})
        precio = i.get("precio_unitario")
        if precio is None:
            precio = e.get("precio_unitario")
        precio = _d(precio) if precio is not None else Decimal("0.00")
        if k in por_sku:
            por_sku[k]["cantidad"] += cantidad
            importes[k] += precio * cantidad
            continue
        importes[k] = precio * cantidad
        por_sku[k] = {
            "sku": sku, "cantidad": cantidad, "precio_unitario": float(precio),
            "titulo": (_limpio(i.get("titulo")) or _limpio(e.get("titulo")) or None),
            "imagen": _imagen(i.get("imagen")) or _imagen(e.get("imagen"))}
    for k, r in por_sku.items():
        r["precio_unitario"] = float((importes[k] / r["cantidad"]).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP))
    return list(por_sku.values()), piezas, sin_sku


def _venta(c: dict[str, Any] | None, a: dict[str, Any] | None,
           ligadas: list[dict[str, Any]]) -> dict[str, Any]:
    """Una VentaMarketplace. `c` = la fila de channel.orders (la verdad de la
    venta); `a` = la de Automatización (guía, fotos). Puede faltar una."""
    base = c or a or {}
    a = a or {}
    items = list((c or {}).get("items") or [])
    lineas, piezas, sin_sku = _lineas_venta(items or list(a.get("items") or []),
                                            list(a.get("items") or []))
    total = (c or {}).get("total") if c else a.get("total")
    comision = (c or {}).get("comision") if c else None
    canal, cuenta, orden = str(base.get("canal") or ""), str(base.get("cuenta") or ""), \
        str(base.get("orden") or "")
    ov = next((v for v in ligadas
               if v["mp_orden"] == orden and (v["mp_canal"] or "").lower() == canal.lower()
               and (v["mp_cuenta"] or "").lower() == cuenta.lower()), None)
    return {
        "canal": canal, "cuenta": cuenta, "orden": orden, "fecha": _plano(base.get("fecha")),
        "estado_canal": (c or {}).get("estado_canal"), "estado_wc": (c or {}).get("estado_wc"),
        "cancelada": cancelada_en_canal(canal, (c or {}).get("estado_canal"),
                                        (c or {}).get("estado_wc"), a.get("accion")),
        # FULL / FBA / WFS: el dato fiable está en los RENGLONES, no en el encabezado.
        "es_fulfillment": (any(bool(i.get("es_fulfillment")) for i in items) if items
                           else bool((c or {}).get("es_fulfillment"))),
        "total": None if total is None else float(total),
        "comision": None if comision is None else float(comision),
        # Sin comisión conocida no hay neto: null es «no lo sabemos», no un cero.
        "neto": (None if total is None or comision is None
                 else float(_d(total) - _d(comision))),
        "guia": a.get("guia") or None, "paqueteria": a.get("paqueteria") or None,
        "entrega_limite": None,
        "piezas": piezas, "lineas": lineas, "renglones_sin_sku": sin_sku,
        "ov": {"id": ov["id"], "folio": ov["folio"], "estado": ov["estado"]} if ov else None}


def venta_marketplace(orden: str, canal: str | None = None, cur: Any = None) -> dict[str, Any]:
    """Una venta por su id en el marketplace (0..n: el mismo id puede existir en
    dos cuentas), con sus renglones y su precio, para prellenar un borrador."""
    ref = _limpio(orden)[:80]
    if not ref:
        return {"ok": False, "motivo": "Escribe el número de la venta.", "ventas": []}
    canal_ = _limpio(canal).lower() or None
    params: dict[str, Any] = {"orden": ref, "canal": canal_}
    if _tablas(cur=cur) is False:
        return {"ok": False, "motivo": _MSG_MIGRACION, "ventas": []}
    try:
        filas = _filas(_SQL_VENTAS + " where c.external_order_id = %(orden)s"
                       + (" and c.canal = %(canal)s" if canal_ else "")
                       + " order by c.creado_at desc nulls last limit 20", params, cur)
        ligadas = _ligadas([ref], cur)
    except FaltaMigracion:
        return {"ok": False, "motivo": _MSG_SIN_VENTAS, "ventas": []}
    auto = _automatizacion([ref], cur)
    ventas, vistas = [], set()
    for c in filas:
        k = _llave(c["canal"], c["cuenta"], c["orden"])
        vistas.add(k)
        ventas.append(_venta(c, auto.get(k), ligadas))
    # No está en channel.orders pero Automatización sí la conoce: se arma desde ahí.
    for k, a in auto.items():
        if k not in vistas and (not canal_ or k[0] == canal_):
            ventas.append(_venta(None, a, ligadas))
    if not ventas:
        return {"ok": True, "motivo": "No hay ninguna venta con ese número en kubera.",
                "ventas": []}
    return {"ok": True, "ventas": ventas}


def ventas_pendientes(dias: int = 7, canal: str | None = None, limite: int = 100,
                      cur: Any = None) -> dict[str, Any]:
    """Ventas DROP recientes que todavía no tienen orden propia, con sus
    renglones: lo que la pantalla enseña en «Desde venta de marketplace», para
    que UNA PERSONA elija. Los últimos `dias`, más recientes primero.

    DROP = TODOS sus renglones con `es_fulfillment = false` (el dato fiable está
    en channel.order_items, no en el encabezado) y al menos uno con SKU. Una venta
    con orden propia CANCELADA ya no es pendiente: si alguien la canceló a mano,
    no se vuelve a ofrecer. (Ya no hay «modo barrido»: la generación automática
    es `crear_auto`, y su llamador es el planeador.)"""
    params: dict[str, Any] = {"lim": _entero(limite, 100, 1, 500),
                              "dias": _entero(dias, 7, 1, 90)}
    canal_ = _limpio(canal).lower() or None
    if canal_:
        params["canal"] = canal_
    par = ("i.canal = c.canal and i.cuenta = c.cuenta "
           "and i.external_order_id = c.external_order_id")
    sql = (_SQL_VENTAS + f"""
 where c.canal <> 'general' and c.creado_at >= now() - make_interval(days => %(dias)s)
   {"and c.canal = %(canal)s" if canal_ else ""}
   and coalesce(lower(c.estado_wc), '') <> 'cancelled'
   and coalesce(lower(c.estado_canal), '') not in ('cancelled', 'canceled')
   and not (c.canal = 'temu' and coalesce(c.estado_canal, '') = '3')
   and exists (select 1 from channel.order_items i
                where {par} and i.sku is not null and i.sku::text <> '')
   and not exists (select 1 from channel.order_items i where {par} and i.es_fulfillment)
   and not exists (select 1 from ventas.ov_ordenes v
                    where v.mp_orden = c.external_order_id and v.mp_canal = c.canal
                      and v.borrada_at is null and lower(v.mp_cuenta) = lower(c.cuenta))
 order by c.creado_at desc
 limit %(lim)s""")
    if _tablas(cur=cur) is False:
        return {"ok": False, "motivo": _MSG_MIGRACION, "ventas": []}
    try:
        filas = _filas(sql, params, cur)
    except FaltaMigracion:
        return {"ok": False, "motivo": _MSG_SIN_VENTAS, "ventas": []}
    auto = _automatizacion([f["orden"] for f in filas], cur)
    ventas = []
    for c in filas:
        v = _venta(c, auto.get(_llave(c["canal"], c["cuenta"], c["orden"])), [])
        if not v["cancelada"]:
            ventas.append(v)
    return {"ok": True, "ventas": ventas}
