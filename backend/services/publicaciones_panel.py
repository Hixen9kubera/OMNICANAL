"""
publicaciones_panel.py — Las publicaciones vivas del catálogo, por tienda, con
el margen que dejan AL PRECIO QUE HOY ESTÁN COBRANDO.

Alimenta la pestaña Omnicanal. Contesta tres preguntas y ninguna más:

  1. ¿Qué publicaciones tengo y cuáles están ACTIVAS en cada tienda?
  2. Si vendo una AHORA, a este precio, ¿cuánto gano?
  3. ¿Trae oferta? ¿Desde cuándo?

═══════════════════════════════════════════════════════════════════════════════
POR QUÉ ESTE MARGEN NO ES EL DE ANÁLISIS — y no es un error
═══════════════════════════════════════════════════════════════════════════════

El panel de Análisis calcula el margen REALIZADO: lo que de verdad dejaron las
ventas que ya ocurrieron, contra el precio promedio al que se transaron. Este
módulo calcula el margen PROSPECTIVO: lo que dejaría la próxima venta al precio
que la publicación está cobrando en este momento.

Son dos preguntas distintas y las dos son legítimas. Un SKU que vendió 40 piezas
a $300 el mes pasado y hoy está a $180 por una campaña de ML tiene un margen
realizado sano y un margen prospectivo malo — y es el segundo el que dice si
conviene dejar la campaña encendida. Decisión de Eduardo, 2026-08-24, deliberada
y con el realizado a la vista.

NO "armonizar" los dos números. Si alguna vez se juntan en una pantalla, van con
etiqueta cada uno.

═══════════════════════════════════════════════════════════════════════════════
"ACTIVA" NO ES UN VALOR ÚNICO — se define canal por canal
═══════════════════════════════════════════════════════════════════════════════

Cada marketplace nombra distinto lo mismo, y peor: en tres de ellos la columna
que dice "se puede comprar" NO es la misma que dice "pasó la auditoría". Censo
en producción del 2026-08-24 (`channel.listings`, 21,848 filas):

  mercado_libre   `situacion`  active 851 · paused 3,653 · under_review 199 ·
                               closed 17 · inactive 2 · NULL 267
  amazon          `situacion`  DISCOVERABLE 1,253 · closed 289 · NULL 118 ·
                               BUYABLE 90 · PUBLISHED 48
  tiktok          `status`     DRAFT 497 · DELETED 423 · SELLER_DEACTIVATED 285 ·
                               FAILED 11 · PENDING 7
  temu            `status`     3/2 331 · 4/7 59 · 3/3 47 · 5/None 24
  walmart         `status`     PUBLISHED 207 · UNPUBLISHED 27 · SYSTEM_PROBLEM 1
  general         —            13,121 sin nada; 21 con publicación

Las tres trampas, cada una medida:

  · **`DISCOVERABLE` de Amazon NO es comprable.** Son 1,253 — el grupo más
    grande del canal. Contarlas como activas infla el número de 138 a 1,391,
    diez veces. La regla de la casa es `BUYABLE`/`PUBLISHED`.
    OJO: `channel_read._AMZ_VIVA` SÍ incluye `DISCOVERABLE`, y está bien allá:
    esa rejilla contesta "¿existe en Amazon?", que es otra pregunta. Aquí se
    contesta "¿se puede comprar?". No unificar las dos listas.

  · **TikTok tiene CERO publicaciones activas hoy, no 283.** Su `situacion`
    dice `APPROVED` en 283 filas, pero eso es el resultado de la AUDITORÍA;
    la venta la manda `status`, y las 283 están en `SELLER_DEACTIVATED`. No
    existe un solo `ACTIVATE`. Leer `situacion` aquí reportaría 283
    publicaciones que nadie puede comprar — la misma trampa que DISCOVERABLE.
    La constante vive en `tiktok_panel.ESTADO_VIVO`, no se re-declara aquí.

  · **Temu no distingue activo de inactivo.** Contesta con números
    (`status4VO`/`subStatus4VO`) y su cubeta `4/7` es literalmente
    "Activo o inactivo" en su propio Seller Center. Por eso sus 59 filas salen
    como `puede_estar_activa` y NUNCA como `activa`: afirmar que venden sería
    inventar. La lista canónica es `temu.VENDIBLES`.

Y la regla que pidió Eduardo, explícita: **un canal sin estado utilizable NO
devuelve 0 en silencio.** Devuelve `sin_estado` con el motivo escrito, porque un
cero se lee como "no hay activas" y eso sería mentira.

═══════════════════════════════════════════════════════════════════════════════
EL MARGEN TIENE UN TECHO DURO: solo Mercado Libre
═══════════════════════════════════════════════════════════════════════════════

`costing.costos_finales` tiene PK `(sku, canal)` (P4) y HOY solo existen filas
con `canal='mercado_libre'` — 4,405, cero en el resto. Medido el 2026-08-24:

  canal           filas   con costo de SU canal
  mercado_libre   4,989   3,071
  amazon          1,798       0   (1,178 tienen costo de ML)
  tiktok          1,223       0   (770)
  temu              461       0   (295)
  walmart           235       0   (156)
  general        13,142       0

El costo de ML EXISTE para muchos de ellos, y usarlo sería fácil y estaría mal:
la comisión y el fee de envío son distintos en cada marketplace, así que un
margen de Amazon calculado con la tarifa de ML es un número falso con cara de
dato. Por eso este módulo **no publica costo de referencia en los canales sin
costo propio**: si el dato no viaja, nadie puede derivar de él un margen que no
existe. Se marca `margen_motivo='sin_costo_del_canal'` y ya.

`margen` NUNCA vale 0 por falta de información. `0` significa "gana cero".
"No sé" se dice con `null` + motivo.

═══════════════════════════════════════════════════════════════════════════════
LOS CUATRO REQUISITOS DE UN MARGEN HONESTO
═══════════════════════════════════════════════════════════════════════════════

No basta con tener costo. Hacen falta cuatro cosas, y la cuarta es la que
sorprende:

  1. `costo_unitario > 0` en `costos_finales` de SU canal.
  2. `pct_comision` no nula (sin comisión no hay resta que hacer).
  3. **`peso > 0` en `costos_validados`** — porque el fee de envío de ML sale de
     una tabla por (peso × tramo de precio) y `costos._peso_efectivo` sustituye
     el peso faltante por **0.5 kg**, que es el segundo renglón más barato de la
     tarifa. Un margen calculado sin peso sale optimista y no avisa.
  4. `precio_vigente > 0`.

Con los cuatro, de las 851 publicaciones ACTIVAS de ML quedan **466 (54.8%)**
con margen. Los 385 que no: 358 sin fila de costos, 19 sin comisión, 8 sin peso.

═══════════════════════════════════════════════════════════════════════════════
EL FEE DE ENVÍO SE RECALCULA — no se lee de `costos_finales`
═══════════════════════════════════════════════════════════════════════════════

Éste es el punto que más fácil se hace mal. `costos_finales.costo_fee_envio` se
calculó al `precio_sugerido`, y el precio real puede estar en OTRO tramo de la
tarifa de ML (los tramos son $0–98.99 / $99–198.99 / $199–298.99 / $299–498.99 /
$499–998.99 / $999+).

Caso real del censo: `ACC-0027-VER` tiene `precio_sugerido` $3,072.44 (tramo
"$999+") y está cobrando $53.55 con oferta (tramo "$0–98.99"). Reusar el fee
guardado le cobraría al margen una tarifa de otro producto.

Se hace lo mismo que hace `costos.aplicar_precio_manual` cuando alguien escribe
un precio a mano — y por la misma razón, escrita ahí: *"El fee de envío se
re-evalúa porque en ML depende del precio."*

═══════════════════════════════════════════════════════════════════════════════
LA OFERTA: tres estados, no dos
═══════════════════════════════════════════════════════════════════════════════

`channel.listings.price_sale` es lo que el comprador PAGA
(`/items/{id}/sale_price`), y NO baja en `price` cuando la promoción la monta
una campaña de ML. La migración 0025 lo dice: *NULL = todavía no observado, NO
"sin descuento"*.

De las 3,029 filas con `price_sale` poblado, **solo 610 traen descuento real**
(`price_sale < price`); las otras 2,419 se observaron y no tenían promoción.
Pintar oferta cada vez que el campo existe pintaría 2,419 ofertas falsas.

  price_sale IS NULL          → `desconocida`  — nadie ha preguntado
  price_sale >= price         → `sin_oferta`   — se miró y no había
  price_sale <  price         → `con_oferta`   — hay descuento vivo

Y **la observación está vieja**: el máximo `price_sale_at` en producción es
2026-08-21 04:46 UTC. Quien las escribe (`services/precios_venta.py`) está
DORMIDO — no lo dispara nadie. Por eso `oferta_vista_at` y `oferta_dias` viajan
SIEMPRE que hay oferta y el contrato con el frontend los marca obligatorios: una
oferta sin fecha al lado se lee como "hoy", y puede llevar días muerta.

Regla 11 de la casa: nada de HTTP aquí. Esto es lectura de kubera y aritmética.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from services import costos
from services import supabase_db as sdb

log = logging.getLogger("omnicanal.publicaciones")

# ── Qué canales entran ────────────────────────────────────────────────────────
#
# `general` (Woo/chunche.shop) queda FUERA del default a propósito: sus 13,142
# filas son el catálogo completo del ETL de fusión, no publicaciones — solo 21
# tienen `listing_id`. Además Woo es la FUENTE del stock y no un canal de venta
# más. Se puede pedir explícitamente con `canal=general`, y entonces el censo
# dice qué es. Nunca se cuela en "todos".
CANALES_VENTA = ("mercado_libre", "amazon", "tiktok", "temu", "walmart")
CANALES_TODOS = CANALES_VENTA + ("general",)

# Solo Mercado Libre tiene motor de costos hoy (P4). Al día que `costos_finales`
# reciba filas de otro canal, basta con sumarlo aquí: el resto del módulo ya
# junta por `f.canal = l.canal`.
CANALES_CON_COSTO = ("mercado_libre",)

# ── Estados normalizados ──────────────────────────────────────────────────────
#
# El vocabulario que ve el frontend. Cerrado: si aparece un valor nuevo del
# canal cae en `desconocido` con el crudo al lado, nunca se aplasta a "activa".
ACTIVA = "activa"                      # se puede comprar AHORA
PUEDE_ESTAR_ACTIVA = "puede_estar_activa"   # el canal no distingue (Temu)
NO_COMPRABLE = "no_comprable"          # existe y se ve, pero no se vende (Amazon DISCOVERABLE)
PAUSADA = "pausada"
EN_REVISION = "en_revision"
BORRADOR = "borrador"
RECHAZADA = "rechazada"
CERRADA = "cerrada"
SIN_ESTADO = "sin_estado"              # el canal no reporta — NO es "no hay"
DESCONOCIDO = "desconocido"            # valor nuevo que nadie ha mapeado

# Qué cuenta como "activa" para el filtro de la pestaña. `puede_estar_activa`
# entra porque es lo más cerca de la verdad que Temu permite, pero viaja con su
# propia etiqueta para que el número no se lea como afirmación.
ESTADOS_VIVOS = (ACTIVA, PUEDE_ESTAR_ACTIVA)


def _estado_ml(situacion: str | None, _status: str | None) -> str:
    return {
        "active": ACTIVA,
        "paused": PAUSADA,
        "under_review": EN_REVISION,
        "closed": CERRADA,
        "inactive": PAUSADA,   # ML lo usa para publicaciones apagadas; 2 filas
    }.get((situacion or "").lower(), SIN_ESTADO if not situacion else DESCONOCIDO)


def _estado_amazon(situacion: str | None, _status: str | None) -> str:
    # `situacion` es la buyability de la oferta. `status` es el resultado del
    # envío del listing (PUBLISHED/INVALID/…) y NO dice si se puede comprar.
    return {
        "BUYABLE": ACTIVA,
        "PUBLISHED": ACTIVA,
        "DISCOVERABLE": NO_COMPRABLE,   # 1,253 — visible en el catálogo, sin buy box
        "CLOSED": CERRADA,
    }.get((situacion or "").upper(), SIN_ESTADO if not situacion else DESCONOCIDO)


def _estado_tiktok(_situacion: str | None, status: str | None) -> str:
    from services.tiktok_panel import ESTADO_VIVO   # "ACTIVATE"
    s = (status or "").upper()
    if s == ESTADO_VIVO:
        return ACTIVA
    return {
        "DRAFT": BORRADOR,
        "PENDING": EN_REVISION,
        "FAILED": RECHAZADA,
        "SELLER_DEACTIVATED": PAUSADA,   # pasó auditoría y el vendedor la apagó
        "DELETED": CERRADA,
    }.get(s, SIN_ESTADO if not status else DESCONOCIDO)


def _estado_temu(_situacion: str | None, status: str | None) -> str:
    from services.temu import ESTADOS, VENDIBLES
    codigo = status or ""
    if codigo in VENDIBLES:
        return PUEDE_ESTAR_ACTIVA
    if codigo == "5/None":
        return BORRADOR
    if codigo in ESTADOS:
        return PAUSADA          # las cubetas "Incompleto": existen y no venden
    return SIN_ESTADO if not codigo else DESCONOCIDO


def _estado_walmart(_situacion: str | None, status: str | None) -> str:
    from services.walmart_panel import ESTADO_VIVO   # "PUBLISHED"
    s = (status or "").upper()
    if s == ESTADO_VIVO:
        return ACTIVA
    return {
        "UNPUBLISHED": PAUSADA,
        "SYSTEM_PROBLEM": RECHAZADA,
    }.get(s, SIN_ESTADO if not status else DESCONOCIDO)


def _estado_general(_situacion: str | None, _status: str | None) -> str:
    # Woo no es canal de venta: su fila describe el catálogo, no una publicación.
    return SIN_ESTADO


_NORMALIZADORES = {
    "mercado_libre": _estado_ml,
    "amazon": _estado_amazon,
    "tiktok": _estado_tiktok,
    "temu": _estado_temu,
    "walmart": _estado_walmart,
    "general": _estado_general,
}

# Por qué un canal puede no reportar estado. Viaja en el censo para que el
# frontend NUNCA tenga que pintar un 0 sin explicación.
NOTA_CANAL = {
    "temu": "Temu contesta con códigos numéricos y su cubeta 4/7 es literalmente "
            "'Activo o inactivo': no distingue una cosa de la otra ni por API ni "
            "en su Seller Center. Por eso se dice 'puede estar activa'.",
    "tiktok": "En TikTok la venta la manda `status` (ACTIVATE), no `situacion`. "
              "Las 283 marcadas APPROVED pasaron la auditoría pero están "
              "SELLER_DEACTIVATED: existen y no se pueden comprar.",
    "amazon": "DISCOVERABLE (1,253) se ve en el catálogo pero NO se puede "
              "comprar. Activa = BUYABLE o PUBLISHED.",
    "general": "Woo (chunche.shop) es la FUENTE del catálogo y del stock, no un "
               "canal de venta: 13,121 de sus filas son el registro del "
               "catálogo, no publicaciones.",
}


def normalizar_estado(canal: str, situacion: str | None,
                      status: str | None) -> str:
    fn = _NORMALIZADORES.get(canal)
    return fn(situacion, status) if fn else DESCONOCIDO


# ── Oferta ────────────────────────────────────────────────────────────────────

OFERTA_CON = "con_oferta"
OFERTA_SIN = "sin_oferta"
OFERTA_DESCONOCIDA = "desconocida"


def _oferta(price: Any, price_sale: Any, price_sale_at: Any) -> dict[str, Any]:
    """
    Los tres estados de la oferta. `desconocida` NO es `sin_oferta`: significa
    que nadie le ha preguntado a ML por el precio de campaña de esa publicación.
    """
    p = _num(price)
    ps = _num(price_sale)
    if ps is None:
        return {"oferta_estado": OFERTA_DESCONOCIDA, "oferta_precio": None,
                "oferta_desc_pct": None, "oferta_vista_at": None,
                "oferta_dias": None}
    dias = None
    if isinstance(price_sale_at, datetime):
        ref = price_sale_at if price_sale_at.tzinfo else price_sale_at.replace(
            tzinfo=timezone.utc)
        dias = round((datetime.now(timezone.utc) - ref).total_seconds() / 86400, 1)
    hay = p is not None and p > 0 and ps < p
    return {
        "oferta_estado": OFERTA_CON if hay else OFERTA_SIN,
        "oferta_precio": round(ps, 2) if hay else None,
        "oferta_desc_pct": round((p - ps) / p, 4) if hay else None,
        "oferta_vista_at": price_sale_at,
        "oferta_dias": dias,
    }


# ── Margen ────────────────────────────────────────────────────────────────────

SIN_COSTO_CANAL = "sin_costo_del_canal"
SIN_COMISION = "sin_comision"
SIN_PESO = "sin_peso"
SIN_PRECIO = "sin_precio"


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def margen_de(*, precio: Any, costo_unitario: Any, pct_comision: Any,
              peso: Any, largo: Any = 0, ancho: Any = 0, alto: Any = 0,
              canal: str = "mercado_libre") -> dict[str, Any]:
    """
    El desglose de UNA publicación al precio que HOY cobra.

    Es la misma aritmética de `costos.aplicar_precio_manual`, hacia atrás desde
    un precio dado: comisión sobre el precio sin IVA, fee de envío RE-EVALUADO
    al precio real, y la ganancia como resta. Se reimplementa aquí en vez de
    llamarla porque aquélla recibe el `pricing` completo de un SKU en el Estudio
    y aquí se recorren cientos de filas ya leídas en bloque; la fórmula es la
    misma y las constantes (`IVA_RATE`, la tarifa) se importan, no se copian.

    Devuelve SIEMPRE las mismas llaves. Cuando no se puede calcular,
    `margen_pct` es None y `margen_motivo` dice por qué. Nunca 0.
    """
    vacio = {"margen_pct": None, "roi": None, "ganancia_neta": None,
             "costo_unitario": None, "costo_comision": None,
             "costo_fee_envio": None, "iva_mnt": None,
             "pct_comision": None, "margen_motivo": None}

    if canal not in CANALES_CON_COSTO:
        return {**vacio, "margen_motivo": SIN_COSTO_CANAL}

    p = _num(precio)
    cu = _num(costo_unitario)
    pct = _num(pct_comision)
    kg = _num(peso)

    if cu is None or cu <= 0:
        return {**vacio, "margen_motivo": SIN_COSTO_CANAL}
    if pct is None:
        return {**vacio, "margen_motivo": SIN_COMISION}
    # Sin peso, `costos._peso_efectivo` mete 0.5 kg de oficio y el fee sale del
    # renglón barato de la tarifa: el margen quedaría optimista sin avisar.
    if kg is None or kg <= 0:
        return {**vacio, "margen_motivo": SIN_PESO}
    if p is None or p <= 0:
        return {**vacio, "margen_motivo": SIN_PRECIO}

    peso_efectivo, _dims = costos._peso_efectivo(
        kg, _num(largo) or 0, _num(ancho) or 0, _num(alto) or 0)
    fee = costos.calc_fee_envio_ml(peso_efectivo, p)

    precio_sin_iva = p / (1.0 + costos.IVA_RATE)
    comision = round(precio_sin_iva * pct, 2)
    iva = round(p - precio_sin_iva, 2)
    ganancia = round(p - comision - fee - iva - cu, 2)
    return {
        # `margen_pct` va sobre el PRECIO (lo que casi todo el mundo lee como
        # "margen"). `roi` va sobre el COSTO, que es lo que significa el 0.48 de
        # `costos.MARGEN_DEFAULT`. Los dos, con nombre distinto, para que nadie
        # compare peras con manzanas.
        "margen_pct": round(ganancia / p, 4),
        "roi": round(ganancia / cu, 4),
        "ganancia_neta": ganancia,
        "costo_unitario": round(cu, 2),
        "costo_comision": comision,
        "costo_fee_envio": fee,
        "iva_mnt": iva,
        "pct_comision": pct,
        "margen_motivo": None,
    }


# ── Consulta ──────────────────────────────────────────────────────────────────
#
# El join de costos va por `f.canal = l.canal` (P4): una publicación de Amazon
# NO hereda el costo de ML. Es la línea que impide el margen falso.
#
# Se excluyen los "fantasmas" del ETL de fusión —filas-identidad con TODO en
# NULL, mismo filtro que `channel_read.leer_inventario`—: en ML son 266 de las
# 267 filas sin `situacion`.
_BASE = """
select l.sku::text                       as sku,
       l.canal                           as canal,
       a.legacy_code                     as tienda,
       l.listing_id                      as listing_id,
       l.url                             as url,
       l.situacion                       as situacion_cruda,
       l.status                          as status_crudo,
       l.price                           as precio_lista,
       l.price_sale                      as price_sale,
       l.price_sale_at                   as price_sale_at,
       l.currency                        as moneda,
       l.stock_own                       as stock_own,
       l.stock_full                      as stock_full,
       l.stock_fba                       as stock_fba,
       l.is_fulfillment                  as es_full,
       l.updated_at                      as visto_at,
       -- `l.date_published` EXISTE en producción y NO en el sandbox (el clon es
       -- anterior a esa columna). No se pide: la fecha de alta no es parte de
       -- lo que esta pestaña contesta, y depender de una columna que solo está
       -- de un lado convierte cada prueba en sandbox en un 502. Handoff a
       -- omni-datos por la deriva de esquema, 2026-08-24.
       p.name                            as titulo,
       f.costo_unitario                  as costo_unitario,
       f.pct_comision                    as pct_comision,
       f.comision_estimada               as comision_estimada,
       v.peso                            as peso,
       v.largo                           as largo,
       v.ancho                           as ancho,
       v.alto                            as alto
  from channel.listings l
  join core.accounts a on a.id = l.account_id
  left join core.products p on p.sku = l.sku
  left join costing.costos_finales f
         on f.sku = l.sku and f.canal = l.canal
  left join costing.costos_validados v on v.sku = l.sku
 where l.canal = any(%(canales)s)
   and not (nullif(l.listing_id,'') is null and l.situacion is null
            and l.price is null and l.stock_own is null
            and l.logistic_type is null)
"""

def _where_extra(*, cuenta: str | None, search: str | None,
                 params: dict) -> str:
    sql = ""
    if cuenta:
        sql += " and a.legacy_code = %(cuenta)s"
        params["cuenta"] = cuenta
    if search:
        sql += (" and (l.sku::text ilike %(like)s or p.name ilike %(like)s"
                " or l.listing_id ilike %(like)s)")
        params["like"] = f"%{search}%"
    return sql


def _enriquecer(r: dict[str, Any]) -> dict[str, Any]:
    """Fila cruda → fila del panel. Toda la interpretación vive aquí."""
    canal = r["canal"]
    estado = normalizar_estado(canal, r.get("situacion_cruda"),
                               r.get("status_crudo"))
    oferta = _oferta(r.get("precio_lista"), r.get("price_sale"),
                     r.get("price_sale_at"))

    # Precio vigente = lo que el comprador paga. Regla de la migración 0025:
    # coalesce(price_sale, price). Si nunca se observó, lo único que hay es el
    # de lista — y eso es exactamente lo que dice `oferta_estado`.
    pv = _num(r.get("price_sale"))
    if pv is None:
        pv = _num(r.get("precio_lista"))

    m = margen_de(precio=pv, costo_unitario=r.get("costo_unitario"),
                  pct_comision=r.get("pct_comision"), peso=r.get("peso"),
                  largo=r.get("largo"), ancho=r.get("ancho"),
                  alto=r.get("alto"), canal=canal)

    return {
        "sku": r["sku"],
        "titulo": r.get("titulo"),
        "canal": canal,
        "tienda": r.get("tienda"),
        "listing_id": r.get("listing_id"),
        "url": r.get("url"),
        "estado": estado,
        "estado_crudo": r.get("situacion_cruda") or r.get("status_crudo"),
        "precio_lista": (round(_num(r.get("precio_lista")), 2)
                         if _num(r.get("precio_lista")) is not None else None),
        "precio_vigente": round(pv, 2) if pv is not None else None,
        "moneda": r.get("moneda") or "MXN",
        "stock_own": r.get("stock_own"),
        "stock_full": r.get("stock_full"),
        "stock_fba": r.get("stock_fba"),
        "es_full": r.get("es_full"),
        "comision_estimada": r.get("comision_estimada"),
        "visto_at": r.get("visto_at"),
        **oferta,
        **m,
    }


def listar(*, canal: str | None = None, estado: str | None = None,
           solo_activas: bool = False, cuenta: str | None = None,
           search: str | None = None, solo_con_oferta: bool = False,
           orden: str = "sku", page: int = 1,
           per_page: int = 50) -> dict[str, Any]:
    """
    La página de publicaciones. El filtro de estado y el de oferta se aplican
    EN PYTHON porque los dos son derivados (el estado se normaliza fila por fila
    con la tabla de cada canal, y la oferta compara dos columnas); traducirlos a
    SQL sería mantener la misma regla en dos idiomas.

    Para que eso no rompa la paginación, el filtro se aplica sobre el universo
    del canal y la página se corta después. El universo más grande es `general`
    con 13k filas y solo se pide a mano; los canales de venta suman ~8,700.
    """
    canales = [canal] if canal else list(CANALES_VENTA)
    desconocidos = [c for c in canales if c not in CANALES_TODOS]
    if desconocidos:
        raise ValueError(f"canal desconocido: {', '.join(desconocidos)}")

    params: dict[str, Any] = {"canales": canales}
    sql = _BASE + _where_extra(cuenta=cuenta, search=search, params=params)
    filas = [_enriquecer(dict(f)) for f in sdb.fetch_all(sql, params)]

    if solo_activas:
        filas = [f for f in filas if f["estado"] in ESTADOS_VIVOS]
    elif estado:
        filas = [f for f in filas if f["estado"] == estado]
    if solo_con_oferta:
        filas = [f for f in filas if f["oferta_estado"] == OFERTA_CON]

    total = len(filas)
    clave, rev = _clave_orden(orden)
    filas.sort(key=clave, reverse=rev)
    ini = max(0, (page - 1) * per_page)
    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": filas[ini:ini + per_page],
        "cobertura": _cobertura_de(filas, canales),
    }


def _clave_orden(orden: str):
    """(función de orden, descendente). Los None van SIEMPRE al final."""
    campos = {
        "sku": ("sku", False), "precio_desc": ("precio_vigente", True),
        "precio_asc": ("precio_vigente", False),
        "margen_desc": ("margen_pct", True), "margen_asc": ("margen_pct", False),
        "descuento_desc": ("oferta_desc_pct", True),
        "reciente": ("visto_at", True),
    }
    campo, rev = campos.get(orden, ("sku", False))

    # Dos claves distintas para números y textos: Python no compara `str` contra
    # `float`, y una columna como `margen_pct` trae None en más de la mitad de
    # las filas. El primer elemento de la tupla aparta los None; el segundo se
    # normaliza a un tipo único.
    texto = campo in ("sku",)
    # Los None van SIEMPRE al final, en los dos sentidos. Como `sort(reverse=)`
    # invierte la tupla entera, el rango del None tiene que invertirse también:
    # con orden descendente el None debe ser el MENOR para acabar abajo.
    rango_nulo = 0 if rev else 1
    rango_dato = 1 if rev else 0

    def clave(f):
        v = f.get(campo)
        if v is None:
            return (rango_nulo, "" if texto else 0.0)
        if texto:
            return (rango_dato, str(v))
        if isinstance(v, datetime):
            return (rango_dato, v.timestamp())
        return (rango_dato, float(v))

    return clave, rev


# ── Cobertura ─────────────────────────────────────────────────────────────────

def _fila_censo(canal: str) -> dict[str, Any]:
    return {"canal": canal, "publicaciones": 0, "activas": 0,
            "con_margen": 0, "sin_margen": 0, "motivos": {},
            "con_oferta": 0, "sin_oferta": 0, "oferta_desconocida": 0,
            "oferta_mas_vieja_dias": None, "nota": NOTA_CANAL.get(canal)}


def _cobertura_de(filas: list[dict[str, Any]],
                  canales_pedidos: list[str] | None = None) -> dict[str, Any]:
    """
    Cuántas quedan CON margen y cuántas SIN, por canal y con el motivo.

    Va en la misma respuesta que los datos, como en el reporte de Inmovilizado:
    un promedio de margen sin la banda de cobertura al lado se lee como un
    hecho sobre todo el catálogo, y solo lo es sobre la mitad que tiene costo.

    **Un canal SIN filas sigue apareciendo**, en ceros y con su nota. Si
    desapareciera de la lista, la pestaña tendría que pintar la ausencia, y una
    ausencia se lee como "no hay activas" — que es justo la mentira que este
    censo existe para evitar. Lo destapó la prueba en sandbox: ahí no hay
    listings de Temu y `canal=temu` devolvía una lista vacía.
    """
    por_canal: dict[str, dict[str, Any]] = {
        c: _fila_censo(c) for c in (canales_pedidos or [])}
    for f in filas:
        c = por_canal.setdefault(f["canal"], _fila_censo(f["canal"]))
        c["publicaciones"] += 1
        if f["estado"] in ESTADOS_VIVOS:
            c["activas"] += 1
        if f["margen_pct"] is None:
            c["sin_margen"] += 1
            mot = f["margen_motivo"] or "desconocido"
            c["motivos"][mot] = c["motivos"].get(mot, 0) + 1
        else:
            c["con_margen"] += 1
        if f["oferta_estado"] == OFERTA_CON:
            c["con_oferta"] += 1
            d = f.get("oferta_dias")
            if d is not None and (c["oferta_mas_vieja_dias"] is None
                                  or d > c["oferta_mas_vieja_dias"]):
                c["oferta_mas_vieja_dias"] = d
        elif f["oferta_estado"] == OFERTA_SIN:
            c["sin_oferta"] += 1
        else:
            c["oferta_desconocida"] += 1

    canales = sorted(por_canal.values(), key=lambda x: x["canal"])
    for c in canales:
        # Sin publicaciones no hay porcentaje que dar. `None` dice "no aplica";
        # un 0.0 diría "ninguna tiene margen", que no es lo mismo.
        c["pct_con_margen"] = (round(c["con_margen"] / c["publicaciones"], 4)
                               if c["publicaciones"] else None)
    tot = sum(c["publicaciones"] for c in canales)
    con = sum(c["con_margen"] for c in canales)
    return {
        "publicaciones": tot,
        "con_margen": con,
        "sin_margen": tot - con,
        "pct_con_margen": round(con / tot, 4) if tot else None,
        "canales": canales,
        # Se dice aquí y no solo en la documentación porque el que lee la
        # respuesta no lee el módulo.
        "aviso": ("El margen es PROSPECTIVO: contra el precio que la publicación "
                  "cobra hoy, no contra el promedio de las ventas realizadas "
                  "(eso es el panel de Análisis). Solo se calcula donde hay "
                  "costo del PROPIO canal: hoy únicamente Mercado Libre."),
    }


def censo_estados(canal: str | None = None) -> list[dict[str, Any]]:
    """
    Qué estados existen en cada canal y cuántas publicaciones hay en cada uno,
    con el valor CRUDO al lado. Es lo que evita el cero en silencio: si un canal
    no reporta estado, aquí se ve `sin_estado` con su nota, no una lista vacía.
    """
    canales = [canal] if canal else list(CANALES_TODOS)
    filas = sdb.fetch_all(
        """select l.canal, l.situacion, l.status, count(*) as n
             from channel.listings l
            where l.canal = any(%(canales)s)
              and not (nullif(l.listing_id,'') is null and l.situacion is null
                       and l.price is null and l.stock_own is null
                       and l.logistic_type is null)
            group by 1,2,3""", {"canales": canales})
    agg: dict[tuple[str, str], dict[str, Any]] = {}
    for f in filas:
        est = normalizar_estado(f["canal"], f.get("situacion"), f.get("status"))
        k = (f["canal"], est)
        e = agg.setdefault(k, {"canal": f["canal"], "estado": est,
                               "publicaciones": 0, "crudos": {},
                               "nota": NOTA_CANAL.get(f["canal"])})
        e["publicaciones"] += int(f["n"])
        crudo = f.get("situacion") or f.get("status") or "(sin valor)"
        e["crudos"][crudo] = e["crudos"].get(crudo, 0) + int(f["n"])
    # Mismo motivo que en `_cobertura_de`: un canal pedido que no tiene ni una
    # fila aparece en ceros, no desaparece. Que no haya publicaciones y que no
    # se sepa cuáles están activas son dos noticias distintas.
    for c in canales:
        if not any(k[0] == c for k in agg):
            agg[(c, SIN_ESTADO)] = {"canal": c, "estado": SIN_ESTADO,
                                    "publicaciones": 0, "crudos": {},
                                    "nota": NOTA_CANAL.get(c)}
    return sorted(agg.values(), key=lambda x: (x["canal"], -x["publicaciones"]))
