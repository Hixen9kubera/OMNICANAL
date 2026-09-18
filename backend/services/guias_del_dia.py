"""
guias_del_dia.py — Las guías de las órdenes de venta GENERADAS un día, en un
Excel para empacar y en un solo PDF para imprimir.

LA PETICIÓN (Brandon, 15-sep-2026)
──────────────────────────────────
  1. "un botón para poder descargar las guías de todas las órdenes y que me
     permitas seleccionar el día"
  2. "un excel donde primeramente me traerá la orden de venta, las piezas, el
     sku y después la guía; en caso de que una orden de venta tenga una guía
     combinada de otro día que la traiga en el excel y que especifique con un
     color las filas que tengan la misma guía, si son varias que se distingan
     con colores distintos"

QUÉ ES "EL DÍA"
───────────────
El día en que se GENERÓ la orden de venta en Odoo: `creado_at` de la bitácora
(`ops.odoo_sale_orders`), que se escribe una vez y no se re-toca. Se cuenta en
HORA DE LA CIUDAD DE MÉXICO, porque es la hora del almacén: el día 13 va de las
00:00 a las 24:00 de allá, convertido a UTC para preguntarle a la base. Contarlo
en UTC —la hora del servidor— mandaba al día siguiente todo lo generado después
de las 18:00, que es justo la tanda de la tarde.

ENVÍO COMBINADO
───────────────
Dos o más VENTAS del mismo canal con la MISMA guía: Temu las junta en una caja
con una etiqueta cuando el mismo comprador compra varias veces antes del envío,
y las órdenes traen el mismo PDF. La guía se compara sin espacios ni mayúsculas.
Un surtido dividido (una venta, dos órdenes "S1 + S2") NO es combinado.

SURTIDO DIVIDIDO (18-sep-2026)
──────────────────────────────
Una venta que ningún almacén tenía completa nace en DOS órdenes (S38861 en
TEXCO, S38862 en TEXCO II). La bitácora la guarda en una fila con las líneas de
la venta, sin decir qué lleva cada orden; así el Excel ponía todos los SKUs bajo
"S38861 + S38862" con una sola guía. Ahora cada parte es SU orden, con SUS SKUs
y la guía de SU entrega en Odoo (`_leer_partes`), y el PDF saca una etiqueta
por guía distinta: una si la venta va en una caja, dos si va en dos. Si Odoo no
contesta, la venta sale junta, como antes.

Si una orden ACTIVA del día comparte guía con una de OTRO día (S38448 del 12-sep
y S38503 del 13-sep, 22 piezas en una caja), la de otro día SE TRAE y queda
marcada: sin ella el almacén vería media caja. Si la del día está cancelada ya
no hay caja que completar y la de otro día NO se trae (ni su etiqueta).

CÓMO SE NOMBRA Y SE PINTA UN GRUPO — igual en el Excel, la ventana y la pestaña
──────────────────────────────────────────────────────────────────────────────
Antes llevaba letra (A, B…) según su lugar en la lista. La lista cambia de una
vista a otra —la pestaña mira 30 días, el Excel un día, "Ambos" junta canales—,
así que el mismo envío era "B" cian en la pestaña y "A" lavanda en el Excel. Un
empacador con el Excel en la mano leía "B" y pensaba en otra caja.

Ahora el grupo se nombra por su GUÍA —sus últimos 4 caracteres, "…2532", que
son los que se leen en la etiqueta pegada— y su color sale de la guía misma
(`indice_preferido`, FNV-1a), no de la lista. Sólo si dos grupos del mismo
archivo caen en el mismo color, el segundo toma el siguiente libre
(`asignar_colores`): así en un archivo nunca hay dos grupos del mismo color. La
pestaña usa las MISMAS funciones (`frontend/lib/combinados.ts`, gemelas de
éstas y probadas contra éstas).

UNA ETIQUETA QUE TAMBIÉN SALE OTRO DÍA
──────────────────────────────────────
El envío combinado entre el 12 y el 13 sale en el PDF de los dos días. No se
quita solo —una etiqueta que no sale es una caja que no se envía—: se AVISA
(`etiquetas_repetidas`, marcador del PDF, hoja Resumen) y quien descarga puede
pedir omitir las que ya salen en el PDF de un día ANTERIOR.

SÓLO LECTURA
────────────
Aquí no se escribe nada: ni Odoo (sólo `search_read`), ni la base (sólo
`select`). El Excel y el PDF se arman en memoria y se entregan en la respuesta.

DATOS PERSONALES
────────────────
El JSON y el Excel llevan orden, piezas, SKU, guía, paquetería, venta del
canal, canal y fecha: NADA del comprador (la bitácora ni siquiera lo tiene). El
PDF de las etiquetas SÍ trae la dirección —es su propósito, lo imprime el
almacén—: se arma en memoria, nunca toca el disco y nunca se escribe en un log.

TODO BLOQUEA (regla 11)
───────────────────────
psycopg2 y XML-RPC esperan a la red. Se llama desde un endpoint `def` (FastAPI
lo corre en un hilo) o desde `asyncio.to_thread`, nunca dentro de una corrutina.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

log = logging.getLogger("omnicanal.guias_del_dia")

try:
    from zoneinfo import ZoneInfo

    TZ_MX: Any = ZoneInfo("America/Mexico_City")
except Exception:  # noqa: BLE001 — contenedor sin base de zonas horarias
    # México quitó el horario de verano en oct-2022: la CDMX es UTC-6 fijo.
    TZ_MX = timezone(timedelta(hours=-6), "CDMX")

CANALES = ("temu", "tiktok")
ETIQUETA_CANAL = {"temu": "Temu", "tiktok": "TikTok", "todos": "Temu y TikTok"}

# Techo de órdenes de UN día. Hoy son ~40; si algún día pasa de aquí, se pide
# acotar por canal en vez de cortar la lista en silencio: una etiqueta que no
# sale en el PDF es una caja que no se envía.
MAX_ORDENES = 600
# Techo de órdenes de OTROS días que comparten guía. Mismo criterio: pasarse
# responde un error explicado, nunca una lista cortada.
_MAX_OTRO_DIA = 200
_LOTE_ODOO = 200     # ids por consulta de estado (bin_size: no baja binarios)
_LOTE_PDF = 8        # PDFs por consulta: ~80 KB cada uno en base64

# (relleno claro, tinta fuerte). El relleno pinta la fila en Excel con letra
# negra encima; la tinta es el borde y la letra del distintivo en el panel.
#
# OCHO, no doce: el color sale de la guía (no del orden), así que cualquier par
# puede tocar junto y TODOS los pares tienen que distinguirse. Medido en CIELAB:
# la separación mínima entre dos rellenos es ΔE76 = 11.3 (lavanda-azul); con la
# paleta vieja de doce había pares a 4.9 (azul-índigo) y 6.7 (cian-verde
# azulado). Contraste: letra negra sobre relleno ≥ 15.5:1; tinta sobre su
# relleno ≥ 5.0:1 y sobre blanco ≥ 6.7:1.
#
# ⚠️ GEMELA de PALETA_COMBINADO en frontend/lib/combinados.ts: cambiar las dos.
PALETA: tuple[tuple[str, str], ...] = (
    ("E4D7FF", "6D28D9"),   # lavanda
    ("C9EEF4", "155E75"),   # cian
    ("FBD3E6", "9D174D"),   # rosa
    ("D8EFC0", "3F6212"),   # lima
    ("D2E0FC", "1D4ED8"),   # azul
    ("FDE2C2", "9A3412"),   # naranja
    ("FFF2A8", "854D0E"),   # amarillo
    ("CDEBD9", "065F46"),   # menta
)

# Acciones de la bitácora que dicen que la caja NO sale. `solo_registro_cancelar`
# sólo la devuelve `odoo_ventas.cancelar_orden`: el canal canceló y Odoo no se
# tocó (modo observación), así que la orden sigue en 'sale' y sin esto pasaba
# por viva.
_NOTA_CANCELADA = {
    "cancelada": "cancelada en Odoo",
    "ya_cancelada": "cancelada en Odoo",
    "no_se_pudo_cancelar": "venta cancelada en el canal · no enviar",
    "solo_registro_cancelar": "venta cancelada en el canal · no enviar",
}
_NOTA_CANAL = "venta cancelada en el canal · no enviar"


class GuiasError(Exception):
    """Un "no se puede" que se le explica a la persona, no un fallo del sistema."""


class CanalInvalido(ValueError):
    """El canal pedido no existe (400). Cualquier OTRO ValueError es un fallo."""


class DependenciaFaltante(RuntimeError):
    """Falta una librería en el servidor: no es culpa de Odoo ni de la base."""


# ── Ayudantes puros ──────────────────────────────────────────────────────────

def norm_guia(guia: str | None) -> str:
    """La guía para comparar: sin espacios ni mayúsculas (se dictan con espacios)."""
    return re.sub(r"\s+", "", guia or "").lower()


def clave_guia(canal: str, guia: str | None) -> str:
    """`canal|guía normalizada`: la identidad de un envío combinado."""
    return f"{canal}|{norm_guia(guia)}"


def codigo_guia(guia: str | None) -> str:
    """El nombre del grupo: "…2532", los últimos 4 caracteres de la guía."""
    g = norm_guia(guia)
    return f"…{g[-4:].upper()}" if g else ""


def indice_preferido(clave: str) -> int:
    """
    El color que le toca a una guía por sí misma: FNV-1a de 32 bits sobre los
    bytes UTF-8 de su clave, módulo la paleta. No depende de qué más haya en la
    lista, así que el mismo envío pide el mismo color en todas las vistas.
    """
    h = 0x811C9DC5
    for b in clave.encode("utf-8"):
        h = ((h ^ b) * 0x01000193) & 0xFFFFFFFF
    return h % len(PALETA)


def asignar_colores(grupos: list[tuple[str, list[str]]]) -> list[int]:
    """
    Un índice de PALETA por grupo, en el orden dado (el más viejo primero).

    Cada grupo toma su color preferido; si ya lo tomó otro grupo que comparte
    ALGÚN DÍA con él, toma el siguiente libre de esos días. Dos grupos que
    nunca aparecen en el mismo archivo pueden repetir color, y dos que sí
    —todos los de un mismo día— nunca lo repiten: el Excel de un día siempre
    sale con un color por grupo.

    El día importa porque un envío combinado entre el 12 y el 13 sale en los
    dos archivos: si el color se repartiera sólo por lista, el 13 lo pintaría
    de un color y el 12 de otro. `grupos` es [(clave, [días AAAA-MM-DD])].
    """
    n = len(PALETA)
    por_dia: dict[str, set[int]] = {}
    fuera: list[int] = []
    for clave, dias in grupos:
        pref = indice_preferido(clave)
        tomados = set().union(*(por_dia.get(d, set()) for d in dias)) if dias else set()
        idx = next(((pref + k) % n for k in range(n) if (pref + k) % n not in tomados), pref)
        for d in dias:
            por_dia.setdefault(d, set()).add(idx)
        fuera.append(idx)
    return fuera


def hoy_mx() -> date:
    return datetime.now(TZ_MX).date()


def limites_utc(fecha: date) -> tuple[datetime, datetime]:
    """[00:00, 24:00) del día en la Ciudad de México, en UTC."""
    desde = datetime.combine(fecha, time.min, tzinfo=TZ_MX)
    hasta = datetime.combine(fecha + timedelta(days=1), time.min, tzinfo=TZ_MX)
    return desde.astimezone(timezone.utc), hasta.astimezone(timezone.utc)


def _a_mx(valor: Any) -> datetime | None:
    if not valor:
        return None
    if isinstance(valor, str):
        try:
            valor = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(valor, datetime):
        return None
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(TZ_MX)


def ddmm(dia_iso: str) -> str:
    """"2026-09-12" → "12-09"."""
    return f"{dia_iso[8:10]}-{dia_iso[5:7]}" if len(dia_iso or "") >= 10 else ""


def _numero(nombre: str) -> tuple[int, str]:
    """S38339 → (38339, 'S38339'): orden natural, que S9999 no quede tras S10000."""
    m = re.search(r"\d+", nombre or "")
    return (int(m.group()) if m else 1 << 62, nombre or "")


def _partes(nombre: str | None) -> list[str]:
    """Las órdenes de un surtido dividido: la bitácora las une con " + "."""
    return [p.strip() for p in (nombre or "").split(" + ") if p.strip()]


def _lineas(valor: Any) -> list[dict[str, Any]]:
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except ValueError:
            valor = []
    fuera = []
    for ln in valor or []:
        if not isinstance(ln, dict):
            continue
        fuera.append({"sku": str(ln.get("sku") or "").strip(),
                      "piezas": int(ln.get("cantidad") or ln.get("piezas") or 0)})
    return fuera


def _cancelada_en_bitacora(f: dict[str, Any]) -> bool:
    """Lo que ya se sabe SIN Odoo: la acción registrada o el estado del canal."""
    return str(f.get("accion") or "") in _NOTA_CANCELADA or bool(f.get("cancelada_canal"))


# ── Lecturas (bloquean) ──────────────────────────────────────────────────────

# Los fragmentos que deciden QUÉ entra, aparte, para que las pruebas los lean
# tal cual: la ventana del día y la guía normalizada tienen que decir lo mismo
# que `limites_utc` y `norm_guia`.
_SQL_VENTANA = "o.creado_at >= %(desde)s and o.creado_at < %(hasta)s"
_SQL_GUIA_NORM = r"lower(regexp_replace(coalesce(o.guia, ''), '\s+', '', 'g'))"
# SURTIDO DIVIDIDO EN DOS CAJAS: la bitácora guarda "G1 + G2" (una fila por
# venta). Comparada entera, esa fila nunca coincidía con la venta B combinada
# con la caja G1: desde el día de A se traía a B (sus claves salen partidas en
# Python, `_guias_de`), pero desde el día de B no se traía a A. Se compara
# también CADA guía de la fila, con la misma normalización.
_SQL_GUIA_COMPONENTE = (r"exists (select 1 from unnest(string_to_array(coalesce(o.guia, ''), ' + '))"
                        r" as g(parte) where (o.canal || '|' || "
                        r"lower(regexp_replace(g.parte, '\s+', '', 'g'))) = any(%(claves)s))")

# Sólo las columnas que se van a usar. `ops.odoo_sale_orders` no guarda nada del
# comprador, y aun así se nombra campo por campo: un `select *` heredaría lo que
# alguien le agregue mañana a la tabla.
#
# `cancelada_canal`: la venta cancelada en el CANAL, con la misma llave que usa
# `odoo_ventas_log.historial`. La acción de la bitácora no alcanza: al cancelar,
# el upsert la pisa con lo que devolvió `cancelar_orden` —`apagado`, `sin_orden`
# (un surtido dividido nunca se encuentra por ref: sus partes son "id#1",
# "id#2"), un error…— y conserva el id de la orden, que en Odoo sigue en 'sale'.
# `estado_wc = 'cancelled'` lo escribe la propia tubería de pedidos en el mismo
# aviso que dispara la cancelación; en TikTok coincide 1:1 con CANCELLED.
_SQL_BASE = """
    select o.canal, o.cuenta, o.external_order_id, o.odoo_order_id, o.odoo_name,
           o.estado, o.accion, o.almacen, o.guia, o.paqueteria, o.creado_at,
           exists (select 1 from channel.orders c
                    where c.canal = o.canal
                      and c.cuenta = o.cuenta
                      and c.external_order_id = o.external_order_id
                      and (c.estado_wc = 'cancelled'
                           or upper(coalesce(c.estado_canal, '')) = 'CANCELLED'))
             as cancelada_canal,
           coalesce(
             (select json_agg(json_build_object('sku', i.sku, 'cantidad', i.cantidad)
                              order by i.linea)
                from ops.odoo_sale_order_items i
               where i.canal = o.canal
                 and i.cuenta = o.cuenta
                 and i.external_order_id = o.external_order_id),
             '[]'::json) as lineas
      from ops.odoo_sale_orders o
     where o.odoo_order_id is not null
"""


def _leer_dia(desde: datetime, hasta: datetime,
              canales: tuple[str, ...]) -> list[dict[str, Any]]:
    """Las órdenes con orden en Odoo GENERADAS en [desde, hasta)."""
    from services import supabase_db as sdb

    return sdb.fetch_all(
        _SQL_BASE + f"""
       and o.canal = any(%(canales)s)
       and {_SQL_VENTANA}
     order by o.creado_at, o.odoo_name
     limit %(lim)s""",
        {"canales": list(canales), "desde": desde, "hasta": hasta,
         "lim": MAX_ORDENES + 1})


def _leer_misma_guia(claves: list[str], desde: datetime,
                     hasta: datetime) -> list[dict[str, Any]]:
    """Las de OTROS días que comparten guía (misma `canal|guía normalizada`)."""
    if not claves:
        return []
    from services import supabase_db as sdb

    # Una de más que el techo: así se sabe si hubo que cortar.
    return sdb.fetch_all(
        _SQL_BASE + f"""
       and ((o.canal || '|' || {_SQL_GUIA_NORM}) = any(%(claves)s)
            or {_SQL_GUIA_COMPONENTE})
       and not ({_SQL_VENTANA})
     order by o.creado_at, o.odoo_name
     limit %(lim)s""",
        {"claves": list(claves), "desde": desde, "hasta": hasta,
         "lim": _MAX_OTRO_DIA + 1})


_CAMPOS_ODOO = ["name", "state", "meli_etiqueta_file", "meli_etiqueta_filename"]


def _leer_odoo(filas: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """
    Estado VIVO y si hay PDF de cada orden. None = Odoo no respondió.

    Con `bin_size`: Odoo contesta el TAMAÑO del archivo ("76.94 Kb") en vez del
    binario, así que revisar 40 órdenes no baja 3 MB de etiquetas. Por eso aquí
    NO se sabe si el archivo es un PDF legible: eso sólo se descubre al armar
    el PDF, que lo avisa en sus cabeceras (y la ventana lo muestra).

    `search_read` y no `read`: un id que ya no exista en Odoo hace fallar un
    `read` entero; la búsqueda simplemente no lo trae.
    """
    from services import odoo_ventas

    ids = sorted({int(f["odoo_order_id"]) for f in filas if f.get("odoo_order_id")})
    # Las mitades 2, 3… de un surtido dividido: la bitácora sólo guarda el id de
    # la primera, pero el PDF puede estar en cualquiera.
    extras = sorted({n for f in filas for n in _partes(f.get("odoo_name"))[1:]})
    partners = sorted(set(odoo_ventas._PARTNER.values()))  # noqa: SLF001
    ctx = {"bin_size": True}
    vistos: dict[int, dict[str, Any]] = {}
    try:
        for i in range(0, len(ids), _LOTE_ODOO):
            for o in odoo_ventas._kw(  # noqa: SLF001
                    "sale.order", "search_read",
                    [[["id", "in", ids[i:i + _LOTE_ODOO]]]],
                    {"fields": _CAMPOS_ODOO, "context": ctx}) or []:
                vistos[int(o["id"])] = o
        for i in range(0, len(extras), _LOTE_ODOO):
            for o in odoo_ventas._kw(  # noqa: SLF001
                    "sale.order", "search_read",
                    [[["name", "in", extras[i:i + _LOTE_ODOO]],
                      ["partner_id", "in", partners]]],
                    {"fields": _CAMPOS_ODOO, "context": ctx}) or []:
                vistos[int(o["id"])] = o
    except Exception as exc:  # noqa: BLE001 — la vista previa sigue sin el dato
        log.warning("guias_del_dia: Odoo no respondió al revisar los PDF (%s)",
                    str(exc)[:200])
        return None
    return list(vistos.values())


def _leer_partes(filas: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """
    Las órdenes de Odoo de cada surtido dividido, con SUS SKUs y SU guía.
    {(canal, venta): [parte, …]} — sólo las que de verdad salen partidas (≥ 2).

    La bitácora guarda una fila por venta con las líneas de la VENTA sin decir a
    qué orden van, y la guía de la venta ("G1 + G2" si son dos cajas). Para que
    cada SKU quede bajo SU orden y con SU guía se le pregunta a Odoo
    (`odoo_ventas.partes_de_ventas`: sólo `search_read`). Si Odoo no contesta,
    la venta sale como antes: una fila "S1 + S2".
    """
    from services import odoo_ventas

    por_canal: dict[str, list[str]] = {}
    for f in filas:
        if len(_partes(f.get("odoo_name"))) > 1 and f.get("external_order_id"):
            por_canal.setdefault(str(f.get("canal") or ""), []).append(
                str(f["external_order_id"]))
    salida: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for canal, ventas in por_canal.items():
        try:
            res = odoo_ventas.partes_de_ventas(canal, ventas)
        except Exception as exc:  # noqa: BLE001 — la venta sale junta, como antes
            log.warning("guias_del_dia: Odoo no dio las partes de %d surtido(s) "
                        "dividido(s) de %s (%s)", len(ventas), canal, str(exc)[:160])
            continue
        for venta, ps in (res or {}).items():
            if len(ps) > 1:
                salida[(canal, str(venta))] = ps
    return salida


def _guias_de(f: dict[str, Any],
              partes: dict[tuple[str, str], list[dict[str, Any]]]) -> list[str]:
    """Las guías de una fila: la suya (partida en " + " si son varias) y, si es
    surtido dividido, la de cada parte."""
    guias = [g.strip() for g in str(f.get("guia") or "").split(" + ") if g.strip()]
    for p in partes.get((str(f.get("canal") or ""), str(f.get("external_order_id") or "")), []):
        guias += [g.strip() for g in str(p.get("guia") or "").split(" + ") if g.strip()]
    return guias


def _leer_pdfs(ids: list[int]) -> dict[int, bytes]:
    """Los PDFs de etiqueta, en memoria. Nunca se escriben ni se registran."""
    from services import odoo_ventas

    unicos = list(dict.fromkeys(int(i) for i in ids if i))
    fuera: dict[int, bytes] = {}
    for i in range(0, len(unicos), _LOTE_PDF):
        for o in odoo_ventas._kw(  # noqa: SLF001
                "sale.order", "search_read",
                [[["id", "in", unicos[i:i + _LOTE_PDF]]]],
                {"fields": ["meli_etiqueta_file"]}) or []:
            b64 = o.get("meli_etiqueta_file")
            if not b64:
                continue
            try:
                fuera[int(o["id"])] = base64.b64decode(b64)
            except (ValueError, TypeError):
                continue  # se reporta como ilegible más adelante
    return fuera


# ── El armado (puro: se prueba sin red) ──────────────────────────────────────

def _ordenes_de_partes(f: dict[str, Any], llave: tuple[str, str, str], otro: bool,
                       ps: list[dict[str, Any]], por_id: dict[int, dict[str, Any]],
                       nota_accion: str | None) -> list[dict[str, Any]]:
    """
    Un surtido dividido, UNA orden por parte: cada una con SUS SKUs y piezas,
    SU guía (la de su entrega en Odoo) y SU PDF. Así el Excel pone cada SKU
    bajo la orden que de verdad lo lleva, y el PDF saca una etiqueta por guía
    distinta: una si la venta va en una caja, dos si va en dos.

    La guía es la de la ENTREGA de esa parte y nada más. Si todavía no la
    tiene, sale "sin guía": suponerle la de la venta sería imprimirle la
    etiqueta de la otra caja.
    """
    creado = _a_mx(f.get("creado_at"))
    paq_fila = str(f.get("paqueteria") or "").strip()
    salida = []
    for i, p in enumerate(ps, 1):
        oid = int(p.get("odoo_order_id") or 0)
        doc = por_id.get(oid)
        tiene_pdf = bool(doc.get("meli_etiqueta_file")) if doc else bool(p.get("tiene_pdf"))
        estado = (doc or {}).get("state") or p.get("estado")
        # Mismo orden de precedencia que una orden entera (ver `armar`).
        nota_cancel = (nota_accion
                       or ("cancelada en Odoo" if estado == "cancel" else None)
                       or (_NOTA_CANAL if f.get("cancelada_canal") else None))
        guia = str(p.get("guia") or "").strip()
        paq = str(p.get("paqueteria") or "").strip() or (
            paq_fila if guia and paq_fila and " + " not in paq_fila else "")
        lineas = [{"sku": str(l.get("sku") or "").strip(),
                   "piezas": int(l.get("cantidad") or 0)} for l in (p.get("lineas") or [])]
        salida.append({
            "orden": str(p.get("odoo_name") or ""),
            "odoo_ids": [oid] if oid else [],
            "pdf_odoo_id": oid if (tiene_pdf and oid) else None,
            "venta": llave[2],
            "canal": llave[0],
            "cuenta": llave[1],
            "fecha": creado.isoformat() if creado else None,
            "fecha_dia": creado.strftime("%d-%m") if creado else "",
            "almacen": str(p.get("almacen") or ""),
            "guia": guia,
            "paqueteria": paq,
            "lineas": lineas,
            "piezas_total": sum(ln["piezas"] for ln in lineas),
            "grupo": None,
            "otro_dia": otro,
            "tiene_pdf": tiene_pdf,
            "en_odoo": True,
            "cancelada": bool(nota_cancel),
            "nota": "",
            # "1 de 2": qué entrega de la venta es. Sólo existe en un surtido
            # dividido; el resto de las órdenes no trae esta llave.
            "parte": f"{i} de {len(ps)}",
            "_hermanas": [str(q.get("odoo_name") or "") for q in ps if q is not p],
            "_ts": creado.timestamp() if creado else 0.0,
            "_dia": creado.date().isoformat() if creado else "",
            "_cancel": nota_cancel or "",
        })
    return salida


def armar(filas_dia: list[dict[str, Any]], filas_otro: list[dict[str, Any]],
          odoo: list[dict[str, Any]] | None, fecha: date,
          canal: str,
          partes_venta: dict[tuple[str, str], list[dict[str, Any]]] | None = None
          ) -> dict[str, Any]:
    """
    Junta bitácora + Odoo en la lista YA ORDENADA como va en el Excel:
    primero los envíos combinados (del más viejo al más nuevo), luego las demás
    con guía (por número de orden), luego las sin guía y al final las canceladas.

    `partes_venta` ({(canal, venta): [parte]}, de `_leer_partes`) parte cada surtido
    dividido en una orden por parte. Sin él —o si Odoo no contestó— la venta
    partida sale como siempre: una fila "S1 + S2".
    """
    odoo_ok = odoo is not None
    por_id = {int(d["id"]): d for d in (odoo or []) if d.get("id")}
    por_nombre = {str(d.get("name")): d for d in (odoo or []) if d.get("name")}

    ordenes: list[dict[str, Any]] = []
    vistas: set[tuple[str, str, str]] = set()
    for f, otro in [(f, False) for f in filas_dia] + [(f, True) for f in filas_otro]:
        llave = (str(f.get("canal") or ""), str(f.get("cuenta") or ""),
                 str(f.get("external_order_id") or ""))
        if llave in vistas:
            continue
        vistas.add(llave)

        ps = (partes_venta or {}).get((llave[0], llave[2]))
        if ps and len(ps) > 1:
            ordenes.extend(_ordenes_de_partes(
                f, llave, otro, ps, por_id, _NOTA_CANCELADA.get(str(f.get("accion") or ""))))
            continue

        nombre = str(f.get("odoo_name") or "")
        partes = _partes(nombre)
        docs = []
        if f.get("odoo_order_id") and int(f["odoo_order_id"]) in por_id:
            docs.append(por_id[int(f["odoo_order_id"])])
        for n in partes[1:]:
            if n in por_nombre and por_nombre[n] not in docs:
                docs.append(por_nombre[n])
        con_pdf = [d for d in docs if d.get("meli_etiqueta_file")]

        creado = _a_mx(f.get("creado_at"))
        lineas = _lineas(f.get("lineas"))
        accion = str(f.get("accion") or "")
        nota_cancel = _NOTA_CANCELADA.get(accion)
        if not nota_cancel and docs and all(d.get("state") == "cancel" for d in docs):
            nota_cancel = "cancelada en Odoo"
        if not nota_cancel and f.get("cancelada_canal"):
            nota_cancel = _NOTA_CANAL

        ordenes.append({
            "orden": nombre,
            "odoo_ids": [int(d["id"]) for d in docs] or (
                [int(f["odoo_order_id"])] if f.get("odoo_order_id") else []),
            "pdf_odoo_id": int(con_pdf[0]["id"]) if con_pdf else None,
            "venta": llave[2],
            "canal": llave[0],
            "cuenta": llave[1],
            "fecha": creado.isoformat() if creado else None,
            "fecha_dia": creado.strftime("%d-%m") if creado else "",
            "almacen": str(f.get("almacen") or ""),
            "guia": str(f.get("guia") or "").strip(),
            "paqueteria": str(f.get("paqueteria") or "").strip(),
            "lineas": lineas,
            "piezas_total": sum(ln["piezas"] for ln in lineas),
            "grupo": None,
            "otro_dia": otro,
            "tiene_pdf": (bool(con_pdf) if odoo_ok else None),
            "en_odoo": (bool(docs) if odoo_ok else None),
            "cancelada": bool(nota_cancel),
            "nota": "",
            "_ts": creado.timestamp() if creado else 0.0,
            "_dia": creado.date().isoformat() if creado else "",
            "_cancel": nota_cancel or "",
        })

    # ── Envíos combinados ────────────────────────────────────────────────
    por_guia: dict[str, list[dict[str, Any]]] = {}
    for o in ordenes:
        if norm_guia(o["guia"]) and not o["cancelada"]:
            por_guia.setdefault(clave_guia(o["canal"], o["guia"]), []).append(o)
    # Un grupo es del día sólo si tiene al menos UNA orden activa del día. Si
    # la del día se canceló, las de otro día que la acompañaban ya no completan
    # ninguna caja de hoy: ni se traen ni se reimprime su etiqueta.
    multiples = [(k, lst) for k, lst in por_guia.items()
                 if len({(x["cuenta"], x["venta"]) for x in lst}) >= 2
                 and any(not x["otro_dia"] for x in lst)]
    # Del más viejo al más nuevo: es el orden de las filas y de quién escoge
    # color primero (ver `asignar_colores`).
    multiples.sort(key=lambda kl: (min(x["_ts"] for x in kl[1]), kl[0]))
    dias_de = {k: sorted({x["_dia"] for x in lst if x["_dia"]}) for k, lst in multiples}
    colores = asignar_colores([(k, dias_de[k]) for k, _ in multiples])

    grupos: list[dict[str, Any]] = []
    for i, ((clave, lst), idx) in enumerate(zip(multiples, colores)):
        lst.sort(key=lambda x: (x["_ts"], _numero(x["orden"])))
        relleno, tinta = PALETA[idx]
        dias = dias_de[clave]
        tambien = sorted({x["_dia"] for x in lst if x["otro_dia"] and x["_dia"]})
        g = {"codigo": codigo_guia(lst[0]["guia"]), "clave": clave,
             "color": f"#{relleno}", "tinta": f"#{tinta}",
             # La escritura más limpia (la guía dictada con espacios es la larga).
             "guia": min((x["guia"] for x in lst), key=lambda s: (len(s), s)),
             "paqueteria": next((x["paqueteria"] for x in lst
                                                        if x["paqueteria"]), ""),
             "canal": lst[0]["canal"], "n": len(lst),
             "piezas": sum(x["piezas_total"] for x in lst),
             "ordenes": [x["orden"] or x["venta"] for x in lst],
             "fechas": [ddmm(d) for d in dias],
             "otro_dia": bool(tambien),
             # Los OTROS días cuyo PDF también trae esta etiqueta.
             "etiqueta_tambien_en": tambien}
        grupos.append(g)
        for x in lst:
            x["_gi"] = i
            x["grupo"] = {"codigo": g["codigo"], "color": g["color"], "tinta": g["tinta"],
                          "n": g["n"], "piezas": g["piezas"],
                          "companeras": [y["orden"] or y["venta"] for y in lst if y is not x],
                          "etiqueta_tambien_en": tambien}

    # Una de otro día sólo entra como compañera de un grupo del día.
    ordenes = [o for o in ordenes if not o["otro_dia"] or o["grupo"]]

    # ── Notas ────────────────────────────────────────────────────────────
    for o in ordenes:
        notas = []
        if o.get("parte"):
            hermanas = [h for h in o.get("_hermanas") or [] if h]
            notas.append(f"surtido dividido · entrega {o['parte']}"
                         + (f" · con {', '.join(hermanas)}" if hermanas else ""))
        if o["otro_dia"]:
            notas.append(f"otro día ({o['fecha_dia']})" if o["fecha_dia"] else "otro día")
        if o["cancelada"]:
            notas.append(o["_cancel"])
        else:
            if not o["guia"]:
                notas.append("sin guía")
            if odoo_ok and not o["en_odoo"]:
                notas.append("no se encontró en Odoo")
            elif o["tiene_pdf"] is False:
                g = norm_guia(o["guia"])
                cubre = next((x for x in ordenes if g and x is not o and not x["cancelada"]
                              and x["canal"] == o["canal"] and norm_guia(x["guia"]) == g
                              and x["tiene_pdf"]), None)
                notas.append(f"sin PDF (se imprime la de {cubre['orden']})" if cubre
                             else "sin PDF")
            elif o["tiene_pdf"] is None:
                notas.append("PDF sin verificar")
        o["nota"] = " · ".join(notas)

    def _clave(o: dict[str, Any]) -> tuple:
        if o["grupo"]:
            return (0, o["_gi"], o["_ts"], _numero(o["orden"]))
        cubeta = 3 if o["cancelada"] else 1 if o["guia"] else 2
        return (cubeta, 0, 0.0, _numero(o["orden"]))

    ordenes.sort(key=_clave)
    for o in ordenes:
        for k in ("_ts", "_cancel", "_gi", "_dia", "_hermanas"):
            o.pop(k, None)

    plan = plan_etiquetas(ordenes, fecha)
    activas = [o for o in ordenes if not o["cancelada"]]
    de_otro = sum(1 for o in ordenes if o["otro_dia"])
    repetidas = [e for e in plan if e["pdf_odoo_id"] and e["tambien_en"]]
    resumen = {
        "total": len(ordenes),
        "del_dia": len(ordenes) - de_otro,
        "de_otro_dia": de_otro,
        "con_guia": sum(1 for o in ordenes if o["guia"]),
        "sin_guia": sum(1 for o in ordenes if not o["guia"]),
        "sin_pdf": sum(1 for o in activas if o["tiene_pdf"] is False),
        "combinados": len(grupos),
        "canceladas": len(ordenes) - len(activas),
        "piezas": sum(o["piezas_total"] for o in activas),
        "etiquetas": sum(1 for e in plan if e["pdf_odoo_id"]),
        "sin_etiqueta": sum(1 for e in plan if not e["pdf_odoo_id"]) if odoo_ok else 0,
        # De `etiquetas`, las que TAMBIÉN salen en el PDF de otro día, y de ésas
        # las de un día ANTERIOR (las que se pueden omitir al descargar).
        "etiquetas_otro_dia": len(repetidas),
        "etiquetas_dia_anterior": sum(1 for e in repetidas if e["anterior"]),
    }
    return {
        "fecha": fecha.isoformat(),
        "canal": canal,
        "zona": "America/Mexico_City",
        "odoo_ok": odoo_ok,
        "ordenes": ordenes,
        "grupos": grupos,
        "resumen": resumen,
        # Las guías cuyo PDF NO va a salir porque Odoo no tiene archivo: se
        # avisan ANTES de descargar. Un archivo que existe pero no es un PDF
        # legible sólo se descubre al armarlo (cabecera X-Guias-Faltantes-Ordenes).
        "faltantes_pdf": ([{"ordenes": e["ordenes"], "guia": e["guia"], "canal": e["canal"]}
                           for e in plan if not e["pdf_odoo_id"]] if odoo_ok else []),
        "etiquetas_repetidas": [{"ordenes": e["ordenes"], "guia": e["guia"],
                                 "codigo": codigo_guia(e["guia"]), "canal": e["canal"],
                                 "tambien_en": e["tambien_en"], "anterior": e["anterior"]}
                                for e in repetidas],
    }


def plan_etiquetas(ordenes: list[dict[str, Any]],
                   fecha: date | str | None = None) -> list[dict[str, Any]]:
    """
    UNA etiqueta por guía distinta, en el orden del Excel.

    Un envío combinado se imprime una vez: dos copias de la misma guía son dos
    cajas con la misma etiqueta y la segunda no viaja. La orden sin guía que sí
    tiene PDF se imprime sola (no hay con quién juntarla). Las canceladas no
    se imprimen.

    `tambien_en`: los otros días (AAAA-MM-DD, hora de México) cuyo PDF también
    trae la etiqueta —los de sus órdenes de otro día—; `anterior`: si alguno es
    anterior a `fecha`.
    """
    dia_ref = fecha.isoformat() if isinstance(fecha, date) else str(fecha or "")
    plan: list[dict[str, Any]] = []
    por_clave: dict[str, dict[str, Any]] = {}
    for o in ordenes:
        if o.get("cancelada"):
            continue
        g = norm_guia(o.get("guia"))
        # Sin guía, la etiqueta es de la venta… salvo en un surtido dividido,
        # donde cada parte puede traer SU caja y SU PDF.
        k = (f"{o['canal']}|{g}" if g
             else f"venta|{o['canal']}|{o.get('cuenta', '')}|{o['venta']}"
             + (f"|{o.get('orden')}" if o.get("parte") else ""))
        e = por_clave.get(k)
        if e is None:
            e = {"clave": k, "guia": o.get("guia") or "", "canal": o["canal"],
                 "ordenes": [], "pdf_odoo_id": None, "_otros": set()}
            por_clave[k] = e
            plan.append(e)
        e["ordenes"].append(o.get("orden") or o["venta"])
        if not e["pdf_odoo_id"] and o.get("pdf_odoo_id"):
            e["pdf_odoo_id"] = int(o["pdf_odoo_id"])
        if o.get("otro_dia") and o.get("fecha"):
            e["_otros"].add(str(o["fecha"])[:10])
    for e in plan:
        e["tambien_en"] = sorted(e.pop("_otros"))
        e["anterior"] = bool(dia_ref) and any(d < dia_ref for d in e["tambien_en"])
    return plan


def dia(fecha: date, canal: str = "todos") -> dict[str, Any]:
    """
    Las órdenes generadas `fecha` (hora de México) + las de otros días que
    comparten guía con ellas. ⚠️ BLOQUEA: llamar desde un hilo.
    """
    canal = (canal or "todos").strip().lower()
    if canal not in (*CANALES, "todos"):
        raise CanalInvalido(f"canal inválido: {canal}")
    canales = CANALES if canal == "todos" else (canal,)
    desde, hasta = limites_utc(fecha)

    filas = _leer_dia(desde, hasta, canales)
    if len(filas) > MAX_ORDENES:
        raise GuiasError(
            f"El {fecha:%d-%m-%Y} tiene más de {MAX_ORDENES} órdenes"
            + (": elige un solo canal." if canal == "todos"
               else ". Pide ayuda para bajarlas por partes."))
    # SURTIDO DIVIDIDO: cada orden con SUS SKUs y SU guía, leídas de Odoo.
    partes = _leer_partes(filas)
    # Sólo las guías de órdenes que siguen vivas: la de otro día que compartía
    # guía con una cancelada ya no completa ninguna caja. (Lo cancelado en Odoo
    # a mano se ve hasta leer Odoo; `armar` lo filtra de nuevo.) Una venta
    # partida en dos cajas aporta sus DOS guías.
    claves = sorted({clave_guia(f["canal"], g)
                     for f in filas if not _cancelada_en_bitacora(f)
                     for g in _guias_de(f, partes) if norm_guia(g)})
    otras = _leer_misma_guia(claves, desde, hasta)
    if len(otras) > _MAX_OTRO_DIA:
        raise GuiasError(
            f"Las órdenes del {fecha:%d-%m-%Y} comparten guía con más de "
            f"{_MAX_OTRO_DIA} órdenes de otros días: algo no cuadra con las guías. "
            "Pide ayuda antes de imprimir.")
    if otras:
        partes.update(_leer_partes(otras))
    odoo = _leer_odoo(filas + otras) if (filas or otras) else []

    datos = armar(filas, otras, odoo, fecha, canal, partes_venta=partes)
    datos["desde_utc"] = desde.isoformat()
    datos["hasta_utc"] = hasta.isoformat()
    r = datos["resumen"]
    log.info("guias_del_dia %s %s: %d órdenes (%d de otro día), %d combinados, "
             "%d sin guía, %d sin PDF, %d etiquetas también en otro día, odoo_ok=%s",
             fecha, canal, r["total"], r["de_otro_dia"], r["combinados"], r["sin_guia"],
             r["sin_pdf"], r["etiquetas_otro_dia"], datos["odoo_ok"])
    return datos


# ── Excel ────────────────────────────────────────────────────────────────────

_COLUMNAS = ("Orden de venta", "Piezas", "SKU", "Guía", "Paquetería",
             "Venta del canal", "Canal", "Fecha de la orden", "Envío combinado", "Nota")
_ANCHOS = (18, 8, 24, 22, 14, 30, 9, 18, 34, 34)
_TEXTO = "@"


def _txt(v: Any) -> str | None:
    """Texto seguro para una celda: sin caracteres de control que rompen el xlsx."""
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    s = ILLEGAL_CHARACTERS_RE.sub("", str(v or "")).strip()
    return s or None


def texto_combinado(o: dict[str, Any]) -> str | None:
    """
    La columna "Envío combinado": "…2532 · con S38448", y en la fila de otro día
    además " · otro día (12-09)". Se repite aquí —no sólo en la Nota— porque la
    Nota es la última columna y en una laptop queda fuera de la pantalla.
    """
    g = o.get("grupo")
    if not g:
        return None
    texto = f"{g['codigo']} · con {', '.join(g['companeras'])}"
    if o.get("otro_dia"):
        texto += f" · otro día ({o['fecha_dia']})" if o.get("fecha_dia") else " · otro día"
    return texto


def excel(datos: dict[str, Any]) -> bytes:
    """
    El .xlsx: hoja "Guías DD-MM-AAAA" con UNA FILA POR SKU y hoja "Resumen".

    La guía, la venta, la orden y el SKU van como TEXTO: `49504479885478` como
    número se vuelve `4.95045E+13` en Excel y ya no sirve para buscar el paquete.
    Las filas de otro día van en CURSIVA y lo dicen en "Envío combinado".
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    fecha = date.fromisoformat(datos["fecha"])
    canal = datos.get("canal") or "todos"
    ordenes = datos.get("ordenes") or []

    negrita_blanca = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    normal = Font(name="Arial", size=10)
    de_otro_dia = Font(name="Arial", size=10, italic=True)
    apagada = Font(name="Arial", size=10, italic=True, color="808080")
    cabecera = PatternFill("solid", fgColor="1F3864")

    wb = Workbook()
    ws = wb.active
    ws.title = f"Guías {fecha:%d-%m-%Y}"
    for c, (titulo, ancho) in enumerate(zip(_COLUMNAS, _ANCHOS), 1):
        cel = ws.cell(1, c, titulo)
        cel.font = negrita_blanca
        cel.fill = cabecera
        cel.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(c)].width = ancho

    r = 2
    for o in ordenes:
        g = o.get("grupo")
        relleno = PatternFill("solid", fgColor=g["color"].lstrip("#")) if g else None
        combinado = texto_combinado(o)
        fuente = apagada if o.get("cancelada") else de_otro_dia if o.get("otro_dia") else normal
        creado = _a_mx(o.get("fecha"))
        lineas = o.get("lineas") or [{"sku": "", "piezas": o.get("piezas_total") or 0}]
        for ln in lineas:
            valores = (_txt(o.get("orden")), int(ln.get("piezas") or 0), _txt(ln.get("sku")),
                       _txt(o.get("guia")), _txt(o.get("paqueteria")), _txt(o.get("venta")),
                       ETIQUETA_CANAL.get(o.get("canal"), o.get("canal")),
                       creado.replace(tzinfo=None) if creado else None,
                       _txt(combinado), _txt(o.get("nota")))
            for c, v in enumerate(valores, 1):
                cel = ws.cell(r, c, v)
                cel.font = fuente
                if c in (1, 3, 4, 6):
                    cel.number_format = _TEXTO
                if relleno is not None:
                    cel.fill = relleno
            ws.cell(r, 2).alignment = Alignment(horizontal="center")
            ws.cell(r, 8).number_format = "dd-mm-yyyy hh:mm"
            r += 1

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(_COLUMNAS))}{max(r - 1, 1)}"

    _hoja_resumen(wb, datos, fecha, canal)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _hoja_resumen(wb: Any, datos: dict[str, Any], fecha: date, canal: str) -> None:
    from openpyxl.styles import Font, PatternFill

    rs = wb.create_sheet("Resumen")
    res = datos.get("resumen") or {}
    rs["A1"] = f"Guías del día {fecha:%d-%m-%Y} · {ETIQUETA_CANAL.get(canal, canal)}"
    rs["A1"].font = Font(name="Arial", size=13, bold=True)
    rs["A2"] = ("Órdenes de venta generadas ese día en Odoo (hora de la Ciudad de "
                "México), más las de otros días que comparten guía con alguna de ellas.")
    rs["A2"].font = Font(name="Arial", size=9, italic=True)

    conteos = (
        ("Órdenes en el archivo", res.get("total", 0)),
        ("Generadas ese día", res.get("del_dia", 0)),
        ("De otro día (comparten guía)", res.get("de_otro_dia", 0)),
        ("Con guía", res.get("con_guia", 0)),
        ("Sin guía", res.get("sin_guia", 0)),
        ("Sin PDF en Odoo", res.get("sin_pdf", 0)),
        ("Envíos combinados", res.get("combinados", 0)),
        ("Canceladas (no se envían)", res.get("canceladas", 0)),
        # Con archivo en Odoo: si alguno no es un PDF legible, lo avisa la
        # descarga del PDF, no esta hoja (aquí no se bajan los archivos).
        ("Etiquetas con archivo en Odoo (una por guía)", res.get("etiquetas", 0)),
        ("   de ellas, también en el PDF de otro día", res.get("etiquetas_otro_dia", 0)),
        ("Guías sin PDF (no salen en el PDF)", res.get("sin_etiqueta", 0)),
        ("Piezas", res.get("piezas", 0)),
    )
    for i, (txt, n) in enumerate(conteos):
        rs.cell(4 + i, 1, txt).font = Font(name="Arial", size=10)
        rs.cell(4 + i, 2, int(n or 0)).font = Font(name="Arial", size=10, bold=True)
    fila = 4 + len(conteos) + 1
    if datos.get("odoo_ok") is False:
        rs.cell(fila, 1, "Odoo no respondió: no se pudo revisar qué órdenes tienen PDF."
                ).font = Font(name="Arial", size=10, bold=True, color="B91C1C")
        fila += 2

    rs.cell(fila, 1, "Leyenda de envíos combinados").font = Font(name="Arial", size=11, bold=True)
    fila += 1
    cabs = ("Grupo (fin de la guía)", "Guía", "Paquetería", "Órdenes", "Piezas en la caja",
            "Fechas", "Canal")
    for c, t in enumerate(cabs, 1):
        rs.cell(fila, c, t).font = Font(name="Arial", size=10, bold=True)
    fila += 1
    grupos = datos.get("grupos") or []
    if not grupos:
        rs.cell(fila, 1, "Sin envíos combinados.").font = Font(name="Arial", size=10, italic=True)
    for g in grupos:
        relleno = PatternFill("solid", fgColor=g["color"].lstrip("#"))
        valores = (g["codigo"], _txt(g.get("guia")), _txt(g.get("paqueteria")),
                   ", ".join(g.get("ordenes") or []), int(g.get("piezas") or 0),
                   ", ".join(g.get("fechas") or []),
                   ETIQUETA_CANAL.get(g.get("canal"), g.get("canal")))
        for c, v in enumerate(valores, 1):
            cel = rs.cell(fila, c, v)
            cel.font = Font(name="Arial", size=10, bold=(c == 1))
            cel.fill = relleno
            if c == 2:
                cel.number_format = _TEXTO
        fila += 1
    for col, ancho in zip("ABCDEFG", (44, 22, 14, 30, 16, 14, 10)):
        rs.column_dimensions[col].width = ancho


# ── PDF ──────────────────────────────────────────────────────────────────────

def pdf(datos: dict[str, Any], leer: Any = None,
        omitir_dia_anterior: bool = False) -> tuple[bytes, dict[str, Any]]:
    """
    UN PDF con las etiquetas en el orden del Excel, una por guía distinta.

    `omitir_dia_anterior`: deja fuera las etiquetas que ya salen en el PDF de un
    día ANTERIOR (envío combinado que empezó otro día). Nunca por omisión.

    Devuelve (bytes, info). `info["faltantes"]` dice qué guías no salieron y por
    qué, `info["omitidas"]` cuáles se dejaron fuera a pedido, con nombres de
    orden —nada del comprador—. Todo en memoria.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as exc:
        raise DependenciaFaltante("pypdf") from exc

    plan = plan_etiquetas(datos.get("ordenes") or [], datos.get("fecha"))
    for e in plan:
        e["omitir"] = bool(omitir_dia_anterior and e["anterior"] and e["pdf_odoo_id"])
    omitidas = [{"ordenes": e["ordenes"], "guia": e["guia"], "canal": e["canal"],
                 "tambien_en": e["tambien_en"]} for e in plan if e["omitir"]]
    if not any(e["pdf_odoo_id"] and not e["omitir"] for e in plan):
        if omitidas:
            raise GuiasError("Todas las etiquetas de ese día ya salen en el PDF de un día "
                             "anterior: descárgalo sin omitirlas si hace falta reimprimir.")
        raise GuiasError("Ninguna orden de ese día tiene el PDF de su guía en Odoo "
                         "(campo \"Subir guía\"): no hay etiquetas que imprimir.")
    binarios = (leer or _leer_pdfs)(
        [e["pdf_odoo_id"] for e in plan if e["pdf_odoo_id"] and not e["omitir"]])

    writer = PdfWriter()
    lectores = []   # vivos hasta escribir: las páginas copiadas pueden apuntarles
    faltantes: list[dict[str, Any]] = []
    etiquetas = 0
    for e in plan:
        if e["omitir"]:
            continue
        base = {"ordenes": e["ordenes"], "guia": e["guia"], "canal": e["canal"]}
        if not e["pdf_odoo_id"]:
            faltantes.append({**base, "motivo": "sin PDF en Odoo"})
            continue
        crudo = binarios.get(e["pdf_odoo_id"]) or b""
        if crudo.lstrip()[:4] != b"%PDF":
            faltantes.append({**base, "motivo": "el archivo de Odoo no es un PDF"})
            continue
        try:
            lector = PdfReader(io.BytesIO(crudo))
            if lector.is_encrypted:
                lector.decrypt("")
            paginas = list(lector.pages)
            if not paginas:
                raise ValueError("PDF sin páginas")
            inicio = len(writer.pages)
            for p in paginas:
                writer.add_page(p)
            lectores.append(lector)
        except Exception as exc:  # noqa: BLE001 — una etiqueta rota no tumba las demás
            # Sólo el TIPO del error: el mensaje de un PDF roto podría citar su
            # contenido, y el contenido es la dirección del comprador.
            log.warning("guias_del_dia: PDF ilegible de %s (%s)",
                        " + ".join(e["ordenes"]), type(exc).__name__)
            faltantes.append({**base, "motivo": "PDF ilegible"})
            continue
        marcador = f"{' + '.join(e['ordenes'])} · {e['guia'] or 'sin guía'}"
        if e["tambien_en"]:
            marcador += " · también en el PDF del " + ", ".join(ddmm(d) for d in e["tambien_en"])
        writer.add_outline_item(marcador, inicio)
        etiquetas += 1

    if not etiquetas:
        raise GuiasError("Ninguno de los PDF de guía de ese día se pudo leer de Odoo.")
    buf = io.BytesIO()
    writer.write(buf)
    log.info("guias_del_dia %s %s: PDF con %d etiqueta(s), %d página(s), %d faltante(s), "
             "%d omitida(s)", datos.get("fecha"), datos.get("canal"), etiquetas,
             len(writer.pages), len(faltantes), len(omitidas))
    return buf.getvalue(), {"etiquetas": etiquetas, "paginas": len(writer.pages),
                            "faltantes": faltantes, "omitidas": omitidas}
