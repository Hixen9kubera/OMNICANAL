"""
inventario_flujo.py — El «Flujo del SKU»: la foto del catálogo por etapas.

Quién la lee HOY: el stepper y el sello de /omnicanal (`/flujo/canal` y el
campo `flujo` de cada producto). La barra que estrenó esta foto arriba de
/inventario se quitó; `/flujo` y `/flujo/skus` siguen publicados, pero ya sin
pantalla que los llame.

QUÉ CONTESTA
------------
¿Cuántos SKUs hay en cada etapa del camino Recibido → Validado bodega → Listo
para FULL o DROP → En FULL | En DROP → Restock, y cuáles son? Con un carril
aparte, Costo validado, que es pregunta comercial y no de almacén.

Y cada etapa dice QUÉ INFORMACIÓN LE FALTA (`falta`), con qué regla cuenta
(`definicion`) y de dónde sale (`fuente`). No es adorno: varias de las cifras
dependen de decisiones de negocio abiertas (D2–D6 del plan del 15-sep), y una
tarjeta que enseña un número sin decir de qué está hecho es cómo se toma una
decisión con algo que no significa lo que parece. Recibido es el ejemplo: el
18-sep pasó de 13,557 (solo el packing list congelado de may–jun) a una unión
con el empaque declarado en Odoo, y NINGUNA de las dos columnas dice «llegó».

POR QUÉ UNA FOTO EN MEMORIA Y NO UNA CONSULTA POR PETICIÓN
---------------------------------------------------------
Contar el catálogo cuesta 0.2–0.7 s de kubera y 12–35 s de Odoo de día. Eso no
cabe en una petición. Así que se arma una FOTO cada 30 min en un HILO PROPIO
(no `asyncio.to_thread`: 12–35 s ocupando uno de los ~6 hilos que comparten el
pool de Supabase y los webhooks de ventas es el apagón del 13-ago en cámara
lenta) y los dos GET solo leen memoria. Un armado a la vez: `_candado` se toma
sin bloquear, y quien llega tarde se va sin esperar.

TRES FUENTES QUE CAEN POR SEPARADO
----------------------------------
`kubera` (una consulta), `odoo` (cuatro lecturas de catálogo más una chica
para desempatar códigos repetidos) y `odoo_drop` (los SKUs del almacén DROP
OFF). Cada una en su propio `try`:

  · Si falla, se conserva su ÚLTIMO RESULTADO BUENO marcado `vieja`.
  · Si no hay último bueno, o tiene más de 2 h (4 × TTL), sus etapas salen
    `n: null` con `estado: "sin_dato"` y un `motivo`.
  · VACÍO NO ES CERO. Catálogo vacío es falla (lo lanza `odoo.py`), y si un
    conjunto cae más del 50 % contra la foto anterior la lectura se marca
    `sospechosa` y se conserva lo anterior. Solo se acepta la caída si la
    SIGUIENTE lectura la repite: una red partida no se repite igual dos veces
    seguidas, un cambio real sí.
  · UNA FUENTE CAÍDA NO RELEE LAS SANAS. El reintento corto (5 min, luego 10,
    20… hasta 30) relee solo las fuentes caídas y conserva tal cual las buenas:
    un DROP caído no puede multiplicar por seis las cuatro lecturas de catálogo
    completo contra el Odoo de producción.

Es la lección de los 964 pedidos fantasma aplicada a una pantalla: un `None`
de una fuente caída no significa «no hay», significa «ya no sé».

LAS REGLAS DE BODEGA SON LAS DE LA TABLA, LITERALMENTE
------------------------------------------------------
Ubicación, stock y foto se deciden con `inventario_maestro.estado_*`, las
mismas funciones que pintan la columna Validado bodega (decisión D2: la
tarjeta y la columna no pueden contradecirse). Y el SKU se cruza con Odoo como
lo cruza la tabla: por `default_code` exacto, archivados incluidos. La prueba
de paridad (`tests/test_inventario_flujo.py`) corre las funciones REALES de la
tabla y del flujo contra el mismo Odoo de mentira.

LO QUE NUNCA SALE DE AQUÍ: DINERO
---------------------------------
Todo lo que cuelga de /api/inventario queda con rol `lectura` (rbac.py). Por
eso estas respuestas llevan SKUs, conteos y fechas — jamás costo, precio,
flete ni margen. El día que haga falta un monto, va en otra ruta con su regla.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from config import settings
from services import inventario_maestro as inv
from services import odoo, supabase_db as sdb

log = logging.getLogger("omnicanal.inventario_flujo")

# La foto vence a la media hora, igual que `odoo._ALMACEN_TTL`: el catálogo se
# mueve despacio y cada armado son 12–35 s de lecturas completas contra Odoo.
TTL_S = 1800
# Pasado esto, un último resultado bueno ya no se sirve: se dice «sin dato».
VIEJA_MAX_S = 4 * TTL_S
# Con alguna fuente caída no se esperan 30 min para reintentar, pero tampoco se
# martilla a Odoo en cada recarga de la pantalla: el primer reintento va a los
# 5 min y cada falla seguida dobla la espera, con tope en el TTL. Se relee SOLO
# la fuente caída (ver `_fuentes_a_leer`).
_REINTENTO_FALLA_S = 300
# Cuánto puede caer un conjunto contra la foto anterior antes de dudar de él.
CAIDA_SOSPECHOSA = 0.5
# Conjuntos chicos se mueven legítimamente; la guarda solo mira los grandes.
_MIN_VIGILADO = 50
# Cuánto se puede parecer la lectura nueva a la sospechosa para confirmarla.
_TOLERANCIA_CONFIRMA = 0.10

# La CUARTA fuente (`canales`) es solo de los conteos POR CANAL: una falla de
# `channel.listings` no puede apagar Recibido ni cambiar la respuesta de
# `/flujo`, que cuenta el catálogo entero y nació con tres fuentes. (El nombre
# `FUENTES_BARRA` es de cuando esa respuesta pintaba la barra de /inventario;
# la barra se quitó, el recorte de tres sigue siendo real.) `_respuesta`
# itera las fuentes de la foto para armar `fuentes` y juzgar `vieja`, y sin este
# recorte una lectura vieja de publicaciones pintaría de ámbar una pantalla que
# no depende de ellas.
FUENTES = ("kubera", "odoo", "odoo_drop", "canales")
FUENTES_BARRA = FUENTES[:3]

ETAPAS_FILTRABLES = frozenset({"recibido", "bodega_3de4", "validado_bodega",
                               "listo_envio", "en_full", "en_fba", "en_drop",
                               "costo_validado"})

# Las únicas que /api/productos sabe convertir en una lista de SKUs exacta. Las
# demás dan 400 con su motivo: `validado_bodega` y `listo_envio` están vacías
# por construcción mientras specs no tenga definición (ver `_META`), `restock`
# no tiene regla y `costo_validado` ya viaja por `revisado=`.
ETAPAS_OMNICANAL = ("recibido", "bodega_3de4", "en_full", "en_fba", "en_drop")

# Las que se leen de la foto (`en_drop` se lee en vivo con `odoo.estado_almacen`).
ETAPAS_DE_FOTO = ("recibido", "bodega_3de4", "en_full", "en_fba")

# Los criterios de conteo, que son los interruptores de la lista y NO el
# contador de la pestaña: en TikTok y Walmart el contador cuenta todas las filas
# mientras «Solo publicados» filtra por `status`, así que un conteo que siguiera
# al contador no cuadraría nunca con la paginación.
CRITERIOS = ("todas", "publicados", "activas")

# LA BODEGA DEL MARKETPLACE ES DE CADA CANAL (Eduardo, 17-sep: «en canales donde
# no haya full cambia su nombre… si no tiene, no debería aparecer»). FULL es de
# Mercado Libre y FBA es de Amazon: son bodegas distintas, con dato distinto
# (`stock_full` contra `stock_fba`), y llamarle FULL a lo de Amazon fue el
# defecto que se corrige aquí.
#
# Los que NO están en este mapa no tienen bodega del marketplace y su segmento
# no se pinta: TikTok y Temu despachan desde nuestro almacén.
#
# WALMART SÍ TIENE BODEGA —WFS es su FULL (`pedidos_walmart.py:166`)— y aun así
# no se pinta, porque NADIE ESCRIBE el dato por SKU: `cargar_walmart.py` es un
# script a mano que guarda listing_id, status, precio y categoría, nunca
# `is_fulfillment` ni stock. Medido el 17-sep: 235 publicaciones, las 235 con
# `is_fulfillment = false` y sin tocarse desde el 17-ago. Un «En WFS · 0» diría
# «ninguno» cuando lo cierto es «no lo sincronizamos». Lo que hoy sí sabe el
# panel de WFS es otra cosa: las SALIDAS a su almacén (pestaña FULLFILMENT, que
# las lee de Odoo) y las ventas despachadas desde ahí (`isWFSEnabled` de cada
# línea de pedido). El día que algo escriba la bandera por SKU, este mapa es el
# único lugar que hay que tocar.
#
# General se queda con FULL de ML porque es el único almacén de marketplace que
# el catálogo entero sabe contar; la etiqueta lo dice.
BODEGA_DEL_CANAL = {
    "general": "en_full",
    "mercado_libre": "en_full",
    "amazon": "en_fba",
}

# UNA consulta, con la forma medida en 0.23–0.68 s (15-sep). Los JOIN van por
# CITEXT NATIVO, sin `::text`: con el cast se pierde `ROP-0695-BEI-m` (la
# mayúscula del costo no empata con la minúscula del catálogo) y la variante
# con subconsulta escalar correlacionada tardó 98 s. El único `::text` es el de
# salida, para que Python reciba un `str` normal.
#
# En FULL es la decisión D3 tal cual se propuso: SOLO Mercado Libre con
# `stock_full > 0`. La columna de la fila todavía suma `stock_full` de todos
# los canales (inventario_maestro.py, `_fila`); esa corrección espera a que D3
# se apruebe, y la diferencia se declara en `falta`.
#
# OJO CON EL NOMBRE: la columna `recibido` de esta consulta es solo la MITAD de
# la etapa Recibido —las cajas del packing list—. La otra mitad (el empaque
# master de Odoo) no vive en kubera y se une en `armar_foto`. Se conserva el
# nombre porque es el conjunto que sale de esta fuente y así lo lee
# `DatosKubera.recibido`; la etapa completa es la unión.
_SQL_KUBERA = """
with cv as (
  select sku,
         bool_or(cajas > 0 and piezas_por_caja > 0) as recibido,
         bool_or(revisado_at is not null)            as costo_validado
  from costing.costos_validados
  group by sku
), ml as (
  select sku from channel.listings
  where canal = 'mercado_libre' and stock_full > 0
  group by sku
), fba as (
  select sku from channel.listings
  where canal = 'amazon' and stock_fba > 0
  group by sku
)
select p.sku::text                        as sku,
       coalesce(cv.recibido, false)       as recibido,
       coalesce(cv.costo_validado, false) as costo_validado,
       (ml.sku is not null)               as en_full,
       (fba.sku is not null)              as en_fba,
       (cv.sku is not null)               as con_renglon,
       p.wc_id                            as wc_id,
       p.wc_parent_id                     as wc_parent_id
from core.products p
left join cv on cv.sku = p.sku
left join ml on ml.sku = p.sku
left join fba on fba.sku = p.sku
"""

# `con_renglon` distingue «no tiene renglón en costos» de «tiene renglón pero
# sin cajas»: son dos huecos distintos y quien los tiene que llenar no es el
# mismo. `wc_id`/`wc_parent_id` son el único puente vivo hacia WooCommerce
# (el seam de core.products), y con ellos General cuenta PRODUCTOS de Woo en vez
# de SKUs de core.products, que es lo que pagina la rejilla.


# ─────────────────────────────────────────────────────────────────────────────
# LA FOTO (inmutable: se reemplaza entera, nunca se edita)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Lectura:
    """Lo que devolvió UNA fuente en UN armado, antes de decidir si se cree."""
    datos: Any = None
    error: str | None = None
    ms: int | None = None
    generado: datetime | None = None


_VACIO: Mapping[Any, Any] = MappingProxyType({})


@dataclass(frozen=True)
class DatosKubera:
    universo: frozenset[str]
    # SOLO la mitad del packing list (cajas y piezas por caja > 0). La etapa
    # Recibido es esto UNIDO al empaque de Odoo (`DatosOdoo.empaque`), y la unión
    # la hace `armar_foto`: aquí no se puede, esta clase solo sabe de kubera.
    recibido: frozenset[str]
    costo_validado: frozenset[str]
    en_full: frozenset[str]
    # Amazon con `stock_fba > 0`: su bodega del marketplace, que NO es FULL.
    en_fba: frozenset[str] = frozenset()
    # SKUs CON renglón en costos_validados (aunque venga en cero).
    con_renglon: frozenset[str] = frozenset()
    # canon → (wc_id, wc_parent_id|None). El 0 de Woo se trata como None: en
    # `core.products` un padre sin padre trae 0, no nulo.
    wc: Mapping[str, tuple[int, int | None]] = _VACIO

    def tamanos(self) -> dict[str, int]:
        # `costo_validado` no se vigila: son ~86 filas y un recálculo masivo de
        # costos puede quitarle el candado a medio conjunto sin que nada esté roto.
        return {"universo": len(self.universo), "recibido": len(self.recibido),
                "en_full": len(self.en_full)}


@dataclass(frozen=True)
class DatosOdoo:
    codigos: frozenset[str]       # todo default_code, archivados incluidos
    activos: frozenset[str]       # códigos con al menos un producto activo
    ubicacion: frozenset[str]     # estado_ubicacion == listo
    stock: frozenset[str]         # estado_stock == listo
    foto: frozenset[str]          # estado_foto == listo
    foto_espera: frozenset[str]   # estado_foto == espera
    con_imagen: frozenset[str]
    # { código: (ubicación, stock, foto) } tal cual lo pinta la columna de la
    # tabla. Sin esto el sello no puede distinguir «foto falta» (producto simple
    # sin imagen) de «foto na» (variante sin hermanos con foto), que es justo la
    # diferencia entre «súbele una foto» y «espera la foto de bodega».
    estados: Mapping[str, tuple[str, str, str]] = _VACIO
    # Códigos con EMPAQUE MASTER declarado (`units_per_master_box > 0`), la
    # segunda mitad de Recibido desde el 18-sep. Es el mismo dato que la columna
    # «piezas por caja» de /inventario, no una regla nueva: dice CÓMO viene
    # empacado el producto, no que haya llegado.
    empaque: frozenset[str] = frozenset()

    def tamanos(self) -> dict[str, int]:
        # `empaque` SÍ se vigila: dentro de core.products son 9,953 SKUs
        # (medición de Eduardo, 18-sep, sobre 22,416) y este conjunto es el de
        # TODOS los códigos de Odoo, o sea aún mayor — muy por encima de
        # `_MIN_VIGILADO`. Desde el 18-sep es la mitad de Recibido, la cifra más
        # grande de la tarjeta. Si el campo llegara vacío para el
        # catálogo entero —un `search_read` que devuelve el campo en `False`
        # por un cambio de permisos, por ejemplo— la etapa se desplomaría sin
        # que ninguna lectura fallara: exactamente el «vacío no es cero» que
        # este módulo existe para impedir. La guarda lo conserva y solo acepta
        # la caída si la SIGUIENTE lectura la repite.
        return {"catalogo": len(self.codigos), "ubicacion": len(self.ubicacion),
                "stock": len(self.stock), "foto": len(self.con_imagen),
                "empaque": len(self.empaque)}


@dataclass(frozen=True)
class DatosCanales:
    """Las publicaciones vivas por canal, cuenta y criterio de lista.

    `publicaciones` es { (canal, cuenta|"*", criterio): { SKU_MAYÚS: filas } }.
    El valor son FILAS y no un booleano porque la paginación de ML sin cuenta
    cuenta publicaciones, no SKUs: «Todas» con 5,205 publicadas son 2,859 SKUs,
    y el número del stepper tiene que cuadrar con el de la rejilla.
    """
    publicaciones: Mapping[tuple[str, str, str], Mapping[str, int]] = _VACIO
    # SKU_MAYÚS → cuentas de ML con stock_full > 0 («En FULL · por San Corpe»).
    full_cuentas: Mapping[str, tuple[str, ...]] = _VACIO
    # Canales que no saben contestar «¿se puede comprar hoy?»: ahí el criterio
    # `activas` se degrada a `todas` y se dice.
    sin_activas: frozenset[str] = frozenset()

    def tamanos(self) -> dict[str, int]:
        # La guarda de «lectura sospechosa» mira el universo de cada canal, que
        # es lo que se desploma cuando el sync se rompe a medias.
        return {c: len(s) for (c, cta, cr), s in self.publicaciones.items()
                if cta == "*" and cr == "todas"}


@dataclass(frozen=True)
class DatosDrop:
    skus: frozenset[str]          # EN MAYÚSCULAS, como los da skus_por_almacen
    edad_s: float

    def tamanos(self) -> dict[str, int]:
        return {"skus": len(self.skus)}


@dataclass(frozen=True)
class Fuente:
    ok: bool
    vieja: bool
    generado: datetime | None
    ms: int | None
    error: str | None
    datos: Any
    sospechosa: bool = False
    # Tamaños de la última lectura RECHAZADA por sospechosa: si la siguiente
    # los repite, la caída es real y se acepta.
    sospecha: Mapping[str, int] | None = None
    # Cuándo se intentó leer por última vez (buena o mala) y cuántas veces
    # seguidas ha fallado. `generado` no sirve para esto: en una falla es la
    # fecha del último resultado BUENO, no la del intento.
    intento: datetime | None = None
    fallas: int = 0


@dataclass(frozen=True)
class Indice:
    """Los mismos conjuntos de la foto, pero buscables en O(1) y sin distinguir
    mayúsculas. Se arma UNA vez por foto: `listas` son tuplas ordenadas y un
    `sku in tupla` es O(n) — con 13,557 en Recibido, 40 tarjetas y sus variantes
    eso deja de ser gratis."""
    kub_mayus: Mapping[str, str] = _VACIO            # SKU_MAYÚS → escritura de core.products
    odoo_mayus: Mapping[str, tuple[str, ...]] = _VACIO   # SKU_MAYÚS → códigos de Odoo
    etapas_mayus: Mapping[str, frozenset[str]] = _VACIO  # etapa → SKUs EN MAYÚSCULAS
    wc_mayus: Mapping[str, tuple[int, int | None]] = _VACIO
    padres_wc: frozenset[int] = frozenset()          # wc_id que son padre de alguien
    hijos_por_wc_padre: Mapping[int, tuple[str, ...]] = _VACIO
    canales: DatosCanales | None = None


@dataclass(frozen=True)
class Foto:
    generado: datetime
    fuentes: Mapping[str, Fuente]
    listas: Mapping[str, tuple[str, ...]]      # etapa → SKUs ordenados
    requisitos: Mapping[str, int | None]       # ubicacion/stock/foto/foto_espera
    cruces: Mapping[str, int | None]           # "etapa.desglose" → n
    universo_n: int | None
    universo_bodega: str                        # "core.products" | "odoo" | ""
    indice: Indice | None = None


# ─────────────────────────────────────────────────────────────────────────────
# REGLAS DE BODEGA SOBRE EL CATÁLOGO COMPLETO (puras)
# ─────────────────────────────────────────────────────────────────────────────

def ids_para_desempate(catalogo: Iterable[Mapping[str, Any]]) -> list[int]:
    """Ids de los productos cuyo código está repetido Y empatado en `active`
    con otro: los únicos donde la tabla decide por existencias. Con un activo y
    archivados gana el activo sin mirar nada más, así que esos no se piden."""
    por_codigo: dict[str, list[Mapping[str, Any]]] = {}
    for p in catalogo:
        if p.get("default_code"):
            por_codigo.setdefault(p["default_code"], []).append(p)
    salida: list[int] = []
    for grupo in por_codigo.values():
        if len(grupo) < 2:
            continue
        arriba = any(bool(p.get("active")) for p in grupo)
        empatados = [p["id"] for p in grupo if bool(p.get("active")) == arriba]
        if len(empatados) > 1:
            salida.extend(empatados)
    return sorted(salida)


def estados_bodega(catalogo: list[dict[str, Any]], quants: Mapping[int, float],
                   libres: set[int] | frozenset[int], fotos: set[int] | frozenset[int],
                   skus: Iterable[str] | None = None,
                   existencias: Mapping[int, tuple[float, float]] | None = None,
                   ) -> dict[str, tuple[str, str, str]]:
    """
    ``{ SKU: (ubicación, stock, foto) }`` con los estados de la tabla, calculados
    en memoria sobre las cuatro lecturas de catálogo de `odoo.py`. Sin `skus`,
    para cada `default_code` del catálogo.

    Cómo se replica lo que la fila le pregunta a Odoo SKU por SKU:

      · SKU → productos por `default_code` EXACTO, archivados incluidos, igual
        que `detalle_por_sku` / `ubicaciones_por_sku` / `miniaturas_por_sku`.
      · UBICACIÓN: la fila junta los quants de TODOS los productos con ese
        código (su búsqueda es por `product_id.default_code`), así que basta
        con que uno aparezca en `quants`.
      · STOCK: la fila se queda con UN producto cuando el código está repetido
        y juzga ese, con la clave de `detalle_por_sku`: (activo, qty_available,
        free_qty). Aquí la MISMA clave, con `existencias` de
        `odoo.existencias_por_id` para los ids de `ids_para_desempate`. No con
        la suma de quants internos: incluye SCRAP y CUARENTENA, que Odoo deja
        fuera de `qty_available`, y elegía otro producto (10 piezas en
        cuarentena le ganaban a 5 en rack). En un empate exacto de la clave la
        tabla toma el primero que devuelve Odoo y aquí el primero por id; da
        igual, porque con el mismo físico y libre el estado es el mismo.
      · FOTO: variantes = hermanos por plantilla (con archivados y SIN filtrar
        código, como `variantes_por_sku`) + hermanos por código base que no
        estén ya entre los de plantilla (como `_sumar_por_codigo`, que busca
        `default_code =like BASE-%`). Diferencias conocidas y aceptadas: la
        tabla corta en `limit 2000` por página y aquí no hay corte; `_` y `%`
        dentro del código son comodines para `=like` y aquí no; y con un código
        duplicado en DOS plantillas la tabla toma solo la última que le devuelve
        Odoo (orden no garantizado) y aquí se unen las dos.
    """
    por_codigo: dict[str, list[dict[str, Any]]] = {}
    por_tmpl: dict[Any, list[dict[str, Any]]] = {}
    por_base: dict[str, list[dict[str, Any]]] = {}
    for p in catalogo:
        cod = p.get("default_code") or ""
        if p.get("tmpl_id") is not None:
            por_tmpl.setdefault(p["tmpl_id"], []).append(p)
        if not cod:
            continue
        por_codigo.setdefault(cod, []).append(p)
        base = odoo._base_de(cod)
        # `=like BASE-%` exige el guion después de la base: `JUGU-1153` NO es
        # hermano por código de `JUGU-1153-MET`, pero `JUGU-1153-MET-B` sí.
        if base and cod.startswith(base + "-"):
            por_base.setdefault(base, []).append(p)

    salida: dict[str, tuple[str, str, str]] = {}
    for sku in (por_codigo.keys() if skus is None else skus):
        propios = por_codigo.get(sku, [])

        # ── ubicación ─────────────────────────────────────────────────────
        ubic = inv.estado_ubicacion(sum(1 for p in propios if p["id"] in quants))

        # ── stock ─────────────────────────────────────────────────────────
        # Odoo ya evaluó «a la mano > 0 Y disponible > 0» del lado del servidor
        # (`odoo._DOMINIO_STOCK_LIBRE`): pertenecer al conjunto es cumplir las
        # dos. Se pasa por la regla compartida para que un cambio de umbral en
        # la tabla rompa la prueba de paridad en vez de pasar callado.
        rep = _representante(propios, existencias)
        libre = rep is not None and rep["id"] in libres
        cumple = 1.0 if libre else 0.0
        stock = inv.estado_stock(cumple, cumple)

        # ── foto ──────────────────────────────────────────────────────────
        tmpls = {p["tmpl_id"] for p in propios if p.get("tmpl_id") is not None}
        hermanos = [h for t in tmpls for h in por_tmpl.get(t, [])
                    if (h.get("default_code") or "") != sku]
        ya = {h.get("default_code") or "" for h in hermanos} | {sku}
        base = odoo._base_de(sku)
        extra = ([c for c in por_base.get(base, []) if c["default_code"] not in ya]
                 if base else [])
        hay_foto = any(p["id"] in fotos for p in propios)
        foto = inv.estado_foto(len(hermanos) + len(extra), hay_foto)

        salida[sku] = (ubic, stock, foto)
    return salida


def _representante(propios: list[dict[str, Any]] | list[Mapping[str, Any]],
                   existencias: Mapping[int, tuple[float, float]] | None,
                   ) -> Mapping[str, Any] | None:
    """El producto que la FILA juzga cuando un `default_code` está repetido.

    La clave es la de `odoo.detalle_por_sku` —`max((previo, nuevo), key=(activo,
    físico, libre))`—, que es de donde la tabla saca stock y piezas por caja.
    Vivía dos veces escrita a mano dentro de este archivo; se extrae porque
    ahora la usan DOS conjuntos (stock y empaque) y una copia que se desincronice
    haría que la tarjeta y la columna dijeran cosas distintas del mismo SKU (D2).

    `existencias` solo trae los ids de `ids_para_desempate` (código repetido Y
    empatado en `active`): con un activo y archivados gana el activo sin mirar
    existencias, igual que la fila."""
    if not propios:
        return None
    ex = existencias or {}
    return max(propios, key=lambda p: (bool(p.get("active")),
                                       *ex.get(p["id"], (0.0, 0.0))))


def codigos_con_empaque(catalogo: Iterable[Mapping[str, Any]],
                        existencias: Mapping[int, tuple[float, float]] | None = None,
                        ) -> set[str]:
    """Los `default_code` con EMPAQUE MASTER declarado: la mitad de Odoo de la
    etapa Recibido (Eduardo, 18-sep). PURA.

    Se lee del MISMO producto que la tabla enseña —el representante— y no de
    «alguno con ese código». La diferencia solo aparece en los códigos
    repetidos (5 SKUs con dos productos ACTIVOS, más los que tienen archivados
    al lado), y ahí un `bool_or` diría «recibido» de un empaque que la columna
    de /inventario no está enseñando: la tarjeta acusaría a la columna de estar
    vacía. Recibido copia lo que la tabla ya muestra, no una lectura propia.

    El criterio es `> 0`, tal cual lo pidió Eduardo. `catalogo_productos` ya
    tradujo el `False` de Odoo a `None`, así que aquí nunca llega un cero
    ambiguo. Los ARCHIVADOS cuentan: la tabla también los enseña (marcados) y
    guardan piezas reales en racks."""
    por_codigo: dict[str, list[Mapping[str, Any]]] = {}
    for p in catalogo:
        cod = p.get("default_code") or ""
        if cod:
            por_codigo.setdefault(cod, []).append(p)
    salida: set[str] = set()
    for cod, propios in por_codigo.items():
        rep = _representante(propios, existencias)
        if rep is not None and (rep.get("piezas_por_caja") or 0) > 0:
            salida.add(cod)
    return salida


# ─────────────────────────────────────────────────────────────────────────────
# ARMADO (puro: sin I/O, se prueba con datos a mano)
# ─────────────────────────────────────────────────────────────────────────────

def _derivar_kubera(filas: list[dict[str, Any]]) -> DatosKubera:
    universo: set[str] = set()
    recibido: set[str] = set()
    costo: set[str] = set()
    full: set[str] = set()
    fba: set[str] = set()
    renglon: set[str] = set()
    wc: dict[str, tuple[int, int | None]] = {}
    for f in filas:
        sku = (f.get("sku") or "").strip()
        if not sku:
            continue
        universo.add(sku)
        if f.get("recibido"):
            recibido.add(sku)
        if f.get("costo_validado"):
            costo.add(sku)
        if f.get("en_full"):
            full.add(sku)
        if f.get("en_fba"):
            fba.add(sku)
        if f.get("con_renglon"):
            renglon.add(sku)
        wc_id = int(f.get("wc_id") or 0)
        if wc_id:
            padre = int(f.get("wc_parent_id") or 0) or None
            wc[sku] = (wc_id, padre)
    return DatosKubera(frozenset(universo), frozenset(recibido),
                       frozenset(costo), frozenset(full), frozenset(fba),
                       con_renglon=frozenset(renglon),
                       wc=MappingProxyType(wc))


def _derivar_canales(crudo: Mapping[str, Any]) -> DatosCanales:
    """Las filas de `channel.listings` agrupadas por (canal, cuenta, criterio).

    Las REGLAS DE INCLUSIÓN son las de cada rejilla, no una simplificación: ML
    hace `join core.accounts` (una fila sin cuenta no se pagina), los tres
    paneles hacen `join core.products` (una publicación fuera del catálogo no
    aparece) y Amazon no filtra ninguna de las dos. Copiarlas mal aquí haría que
    el número del stepper y el total de la lista nunca cuadraran.
    """
    publicaciones: dict[tuple[str, str, str], dict[str, int]] = {}
    full_cuentas: dict[str, set[str]] = {}

    def _sumar(canal: str, cuenta: str, criterio: str, sku: str) -> None:
        cubeta = publicaciones.setdefault((canal, cuenta, criterio), {})
        cubeta[sku] = cubeta.get(sku, 0) + 1

    for f in crudo.get("filas") or []:
        sku = (f.get("sku") or "").strip().upper()
        canal = (f.get("canal") or "").strip()
        if not sku or not canal:
            continue
        cuenta = (f.get("cuenta") or "").strip()
        if canal == "mercado_libre" and not cuenta:
            continue
        if canal in ("tiktok", "temu", "walmart") and not f.get("en_catalogo"):
            continue
        criterios = ["todas"]
        if f.get("publicado"):
            criterios.append("publicados")
        if f.get("activa"):
            criterios.append("activas")
        for cr in criterios:
            _sumar(canal, "*", cr, sku)
            if canal == "mercado_libre":
                _sumar(canal, cuenta, cr, sku)
        if f.get("full") and cuenta:
            full_cuentas.setdefault(sku, set()).add(cuenta)

    return DatosCanales(
        publicaciones=MappingProxyType(
            {k: MappingProxyType(v) for k, v in publicaciones.items()}),
        full_cuentas=MappingProxyType(
            {s: tuple(sorted(c)) for s, c in full_cuentas.items()}),
        sin_activas=frozenset(crudo.get("sin_activas") or ()),
    )


def _derivar_odoo(crudo: Mapping[str, Any]) -> DatosOdoo:
    catalogo = crudo["catalogo"]
    fotos = crudo["fotos"]
    estados = estados_bodega(catalogo, crudo["quants"], crudo["libres"], fotos,
                             existencias=crudo.get("existencias"))
    activos = {p["default_code"] for p in catalogo
               if p.get("default_code") and p.get("active")}
    con_imagen = {p["default_code"] for p in catalogo
                  if p.get("default_code") and p["id"] in fotos}
    # El empaque se decide con la MISMA regla (y el mismo representante) que la
    # columna de /inventario; ver `codigos_con_empaque`.
    empaque = codigos_con_empaque(catalogo, crudo.get("existencias"))
    return DatosOdoo(
        codigos=frozenset(estados),
        activos=frozenset(activos),
        ubicacion=frozenset(s for s, e in estados.items() if e[0] == "listo"),
        stock=frozenset(s for s, e in estados.items() if e[1] == "listo"),
        foto=frozenset(s for s, e in estados.items() if e[2] == "listo"),
        foto_espera=frozenset(s for s, e in estados.items() if e[2] == "espera"),
        con_imagen=frozenset(con_imagen),
        estados=MappingProxyType(estados),
        empaque=frozenset(empaque),
    )


def _derivar_drop(crudo: tuple[Iterable[str], float]) -> DatosDrop:
    skus, edad = crudo
    return DatosDrop(frozenset((s or "").strip().upper() for s in skus if s),
                     float(edad or 0))


def _utilizable(f: Fuente | None, ahora: datetime) -> bool:
    return bool(f is not None and f.datos is not None and f.generado is not None
                and (ahora - f.generado).total_seconds() <= VIEJA_MAX_S)


def _confirma(sospecha: Mapping[str, int] | None, tam: Mapping[str, int],
              caidas: Iterable[str]) -> bool:
    if not sospecha:
        return False
    return all(k in sospecha
               and abs(tam.get(k, 0) - sospecha[k]) <= max(1, _TOLERANCIA_CONFIRMA * sospecha[k])
               for k in caidas)


def _resolver_fuente(lectura: Lectura, anterior: Fuente | None,
                     derivar: Callable[[Any], Any], ahora: datetime) -> Fuente:
    """Decide qué se cree de una fuente: lo nuevo, lo anterior (`vieja`) o nada."""
    previo = anterior if _utilizable(anterior, ahora) else None

    if lectura.error is None:
        try:
            datos = derivar(lectura.datos)
        except Exception as exc:  # noqa: BLE001 — datos malformados = fuente caída
            lectura = Lectura(error=_texto_error(exc), ms=lectura.ms,
                              generado=lectura.generado)
        else:
            generado = lectura.generado or ahora
            # DROP: `skus_por_almacen` sirve su último resultado bueno cuando
            # falla, sin avisar. Si lo que llegó ya pasó el TTL, es eso.
            if isinstance(datos, DatosDrop):
                generado = generado - timedelta(seconds=datos.edad_s)
                if datos.edad_s >= TTL_S:
                    return Fuente(
                        ok=False, vieja=True, generado=generado, ms=lectura.ms,
                        error=(f"Odoo no refrescó el almacén: se sirve la lectura "
                               f"de hace {int(datos.edad_s // 60)} min"),
                        datos=datos)
            if previo is not None:
                tam_ant = previo.datos.tamanos()
                tam = datos.tamanos()
                # Sobre la UNIÓN de llaves, y la que falta cuenta como CERO.
                # `DatosCanales.tamanos()` es la primera con llaves dinámicas:
                # un canal que cae a cero filas desaparece del dict nuevo, y
                # mirando solo las llaves nuevas nunca se comparaba — el
                # desplome total, que es el peor, era el único que pasaba. Es
                # justo el «vacío no es cero» que este módulo existe para
                # impedir.
                caidas = [k for k in sorted(set(tam_ant) | set(tam))
                          if tam_ant.get(k, 0) >= _MIN_VIGILADO
                          and tam.get(k, 0) < tam_ant[k] * (1 - CAIDA_SOSPECHOSA)]
                if caidas and not _confirma(previo.sospecha, tam, caidas):
                    detalle = "; ".join(f"{k} {tam_ant[k]:,} → {tam.get(k, 0):,}"
                                        for k in caidas)
                    return Fuente(
                        ok=False, vieja=True, generado=previo.generado, ms=lectura.ms,
                        error=(f"lectura sospechosa ({detalle}: cae más de 50 %); "
                               f"se conserva la anterior"),
                        datos=previo.datos, sospechosa=True,
                        # La llave que DESAPARECIÓ se guarda como 0: sin ella,
                        # la siguiente lectura idéntica nunca podría confirmar
                        # la caída y la fuente quedaría sospechosa para siempre.
                        sospecha=MappingProxyType(
                            {k: tam.get(k, 0) for k in set(tam) | set(caidas)}))
            return Fuente(ok=True, vieja=False, generado=generado, ms=lectura.ms,
                          error=None, datos=datos)

    if previo is not None:
        return Fuente(ok=False, vieja=True, generado=previo.generado, ms=lectura.ms,
                      error=lectura.error, datos=previo.datos)
    return Fuente(ok=False, vieja=False, generado=None, ms=lectura.ms,
                  error=lectura.error, datos=None)


def _canonicos(skus_mayus: Iterable[str], candidatos: Iterable[str]) -> set[str]:
    """`skus_por_almacen` devuelve MAYÚSCULAS. La tabla busca por `default_code`
    exacto, así que `ROP-0695-BEI-M` no encontraría a `ROP-0695-BEI-m` y la fila
    diría «no existe en Odoo». Se devuelve cada SKU con su escritura real."""
    mapa: dict[str, list[str]] = {}
    for c in candidatos:
        mapa.setdefault(c.upper(), []).append(c)
    salida: set[str] = set()
    for u in skus_mayus:
        salida.update(mapa.get(u) or (u,))
    return salida


def _indice(k: DatosKubera | None, o: DatosOdoo | None,
            canales: DatosCanales | None,
            listas: Mapping[str, Any]) -> Indice:
    """El índice de la foto: todo en MAYÚSCULAS y en dicts/sets. PURO.

    Los conjuntos de la foto traen escrituras MEZCLADAS —kubera la de
    `core.products`, `bodega_3de4` una intersección sensible a mayúsculas y
    `en_drop` la canónica de Odoo—, así que comparar el SKU de una fila contra
    ellos tal cual falla en los 5 SKUs con minúsculas del catálogo."""
    kub_mayus: dict[str, str] = {}
    wc_mayus: dict[str, tuple[int, int | None]] = {}
    padres: set[int] = set()
    hijos: dict[int, list[str]] = {}
    if k is not None:
        for s in k.universo:
            kub_mayus[s.upper()] = s
        for canon, (wc_id, padre) in k.wc.items():
            wc_mayus[canon.upper()] = (wc_id, padre)
            if padre:
                padres.add(padre)
                hijos.setdefault(padre, []).append(canon)

    odoo_mayus: dict[str, list[str]] = {}
    if o is not None:
        for cod in o.codigos:
            odoo_mayus.setdefault(cod.upper(), []).append(cod)

    etapas: dict[str, frozenset[str]] = {
        e: frozenset(s.upper() for s in listas.get(e, ()))
        for e in ETAPAS_FILTRABLES}
    # El cruce que el stepper enseña en «Listo» («si specs no bloqueara»).
    etapas["recibido_y_3de4"] = etapas["recibido"] & etapas["bodega_3de4"]

    return Indice(
        kub_mayus=MappingProxyType(kub_mayus),
        odoo_mayus=MappingProxyType({u: tuple(sorted(v))
                                     for u, v in odoo_mayus.items()}),
        etapas_mayus=MappingProxyType(etapas),
        wc_mayus=MappingProxyType(wc_mayus),
        padres_wc=frozenset(padres),
        hijos_por_wc_padre=MappingProxyType({p: tuple(sorted(v))
                                             for p, v in hijos.items()}),
        canales=canales,
    )


def armar_foto(kubera: Lectura | None, odoo_: Lectura | None, drop: Lectura | None,
               anterior: Foto | None, *, ahora: datetime | None = None,
               canales: Lectura | None = None) -> Foto:
    """La foto nueva a partir de las tres lecturas y la foto anterior. PURA.

    Una lectura `None` quiere decir «esta fuente no se volvió a leer»: su
    `Fuente` anterior se conserva TAL CUAL (con su `intento` y sus `fallas`).
    Es lo que usa el reintento corto para no releer las fuentes sanas."""
    ahora = ahora or _ahora()
    ant = anterior.fuentes if anterior else {}

    def _fuente(nombre: str, lectura: Lectura | None,
                derivar: Callable[[Any], Any]) -> Fuente:
        previa = ant.get(nombre)
        if lectura is None:
            if previa is not None:
                return previa
            lectura = Lectura(error="no se leyó", generado=ahora)
        f = _resolver_fuente(lectura, previa, derivar, ahora)
        return replace(f, intento=ahora,
                       fallas=0 if f.ok else (previa.fallas if previa else 0) + 1)

    fuentes = {
        "kubera": _fuente("kubera", kubera, _derivar_kubera),
        "odoo": _fuente("odoo", odoo_, _derivar_odoo),
        "odoo_drop": _fuente("odoo_drop", drop, _derivar_drop),
        "canales": _fuente("canales", canales, _derivar_canales),
    }
    k: DatosKubera | None = fuentes["kubera"].datos
    o: DatosOdoo | None = fuentes["odoo"].datos
    d: DatosDrop | None = fuentes["odoo_drop"].datos
    c: DatosCanales | None = fuentes["canales"].datos

    listas: dict[str, set[str] | frozenset[str]] = {e: frozenset() for e in ETAPAS_FILTRABLES}
    requisitos: dict[str, int | None] = {"ubicacion": None, "stock": None,
                                         "foto": None, "foto_espera": None}
    cruces: dict[str, int | None] = {}

    if k is not None:
        listas["costo_validado"] = k.costo_validado
        listas["en_full"] = k.en_full
        listas["en_fba"] = k.en_fba

    # RECIBIDO son las DOS columnas que ya enseña la tabla de /inventario
    # (decisión de Eduardo, 18-sep: «para los recibidos por packing list vamos a
    # usar los que hay en inventario nada más»): las cajas del packing list
    # congelado O el empaque master declarado en Odoo. La unión, no la
    # intersección, y acotada al universo de core.products (D6).
    #
    # NO se exige stock libre: eso mediría existencias de hoy, no recepción.
    #
    # `empaque_d6` es la mitad de Odoo YA recortada al universo, y se guarda
    # porque los dos desgloses («solo del packing list», «solo empaque de
    # Odoo») tienen que restar sobre el mismo conjunto que se contó. La
    # intersección es SENSIBLE A MAYÚSCULAS, igual que `bodega_3de4`: un SKU
    # que Odoo escribe distinto queda fuera de la lista y el sello lo dice por
    # su nombre en vez de contradecir al conteo.
    empaque_d6: frozenset[str] = frozenset()
    recibido: frozenset[str] = frozenset()
    if k is not None and o is not None:
        empaque_d6 = o.empaque & k.universo
        recibido = k.recibido | empaque_d6
        listas["recibido"] = recibido
    # Con kubera u Odoo caído la lista se queda VACÍA a propósito y `_META`
    # declara las dos dependencias: media regla daría una cifra a medias, que
    # es justo lo que nadie puede distinguir de una cifra real.

    tres: frozenset[str] = frozenset()
    if o is not None:
        # D6: el universo es core.products. Sin kubera se cuenta sobre los
        # códigos de Odoo — para «listo» da lo mismo, porque un SKU que no está
        # en Odoo nunca cumple ubicación, stock ni foto.
        base = k.universo if k is not None else o.codigos
        tres = o.ubicacion & o.stock & o.foto & base
        # 4 de 4 exige specs; con la regla compartida, hoy es vacío por
        # construcción — y el día que specs exista, deja de serlo sin tocar esto.
        cuatro = tres if inv.estado_specs() == "listo" else frozenset()
        listas["bodega_3de4"] = tres
        listas["validado_bodega"] = cuatro
        requisitos = {"ubicacion": len(o.ubicacion & base),
                      "stock": len(o.stock & base),
                      "foto": len(o.foto & base),
                      "foto_espera": len(o.foto_espera & base)}
        if k is not None:
            listas["listo_envio"] = recibido & cuatro

    if d is not None:
        candidatos = o.codigos if o is not None else (k.universo if k is not None else ())
        listas["en_drop"] = _canonicos(d.skus, candidatos)

    def _n(condicion: bool, valor: Callable[[], int]) -> int | None:
        return valor() if condicion else None

    ko = k is not None and o is not None
    # Los tres cruces de Recibido salen de la UNIÓN, que es lo que cuenta la
    # tarjeta. `fuera_de_odoo` puede traer ahora SKUs de la mitad de Odoo: son
    # los que tienen empaque declarado pero el producto está archivado.
    cruces["recibido.fuera_de_odoo"] = _n(ko, lambda: len(recibido - o.activos))
    # `k.recibido` ya está dentro del universo por construcción (`_derivar_kubera`
    # solo mete SKUs de core.products), así que no hace falta recortarlo otra vez.
    cruces["recibido.solo_packing_list"] = _n(
        ko, lambda: len(k.recibido - empaque_d6))
    cruces["recibido.solo_odoo"] = _n(ko, lambda: len(empaque_d6 - k.recibido))
    cruces["recibido_y_3de4"] = _n(ko, lambda: len(recibido & tres))
    cruces["en_full.cumple_3de4"] = _n(ko, lambda: len(k.en_full & tres))
    cruces["en_full.fuera_de_odoo"] = _n(ko, lambda: len(k.en_full - o.activos))
    cruces["en_fba.cumple_3de4"] = _n(ko, lambda: len(k.en_fba & tres))
    cruces["en_fba.fuera_de_odoo"] = _n(ko, lambda: len(k.en_fba - o.activos))
    cruces["costo_validado.cumple_3de4"] = _n(ko, lambda: len(k.costo_validado & tres))
    cruces["en_drop.cumple_3de4"] = _n(d is not None and o is not None,
                                       lambda: len(set(listas["en_drop"]) & tres))

    return Foto(
        generado=ahora,
        fuentes=MappingProxyType(fuentes),
        listas=MappingProxyType({e: tuple(sorted(v)) for e, v in listas.items()}),
        requisitos=MappingProxyType(requisitos),
        cruces=MappingProxyType(cruces),
        universo_n=len(k.universo) if k is not None else None,
        universo_bodega=("core.products" if k is not None
                         else "odoo" if o is not None else ""),
        indice=_indice(k, o, c, listas),
    )


# ─────────────────────────────────────────────────────────────────────────────
# QUÉ DICE CADA TARJETA (textos a la vista, sin montos)
# ─────────────────────────────────────────────────────────────────────────────

# `depende`: las fuentes sin las cuales la cifra no existe. `desglose`: (clave,
# título, llave en `Foto.cruces`, fuentes). «Cumple lo anterior» va aquí: las
# etapas NO son subconjuntos estrictos (de 499 en FULL, 221 cumplen 3 de 4), así
# que cada tarjeta enseña su propio conteo y además cuántos traen lo de antes.
_META: dict[str, dict[str, Any]] = {
    "recibido": {
        "titulo": "Recibido", "estado": "proxy", "filtrable": True,
        # Las DOS fuentes desde el 18-sep: la cifra es una unión, y con una sola
        # mitad sería una cifra a medias que nadie puede distinguir de la buena.
        "depende": ("kubera", "odoo"),
        "definicion": ("Las dos columnas de /inventario: cajas y piezas por caja "
                       "mayores que 0 en costos validados, O empaque master "
                       "declarado en Odoo (units_per_master_box > 0). Unión, no "
                       "intersección; no se exige stock libre. Los que no existen "
                       "en Odoo activo cuentan dentro y se desglosan aparte "
                       "(decisión de Eduardo, 18-sep)"),
        "fuente": ("kubera · costing.costos_validados (congelado de las cargas del "
                   "21-may y 3-jun) + Odoo · product.product.units_per_master_box"),
        "desglose": [
            ("solo_packing_list", "Solo del packing list",
             "recibido.solo_packing_list", ("kubera", "odoo")),
            ("solo_odoo", "Solo empaque de Odoo",
             "recibido.solo_odoo", ("kubera", "odoo")),
            ("fuera_de_odoo", "No existen en Odoo activo",
             "recibido.fuera_de_odoo", ("kubera", "odoo")),
        ],
        "falta": [
            "NINGUNA DE LAS DOS COLUMNAS DICE «LLEGÓ». El packing list es un "
            "congelado de las cargas del 21-may y 3-jun —dice lo que el proveedor "
            "embarcó— y el empaque de Odoo dice CÓMO viene empacado el producto, "
            "no que haya entrado a la bodega. La etapa sigue siendo un proxy",
            "Cajas y piezas por caja por SKU y contenedor de lo llegado después "
            "del 3-jun: nada escribe esas columnas desde el 27-jul (Compras o "
            "logística)",
            "Dónde guardarlo: la tabla es por SKU y un SKU llega en varios "
            "contenedores; hace falta una por (sku, contenedor) (Eduardo, diseño)",
            "Campo packing_list de las órdenes de compra en Odoo: vacío o texto "
            "libre en buena parte de las confirmadas (quien captura compras)",
            "Acceso a la API de Drive por carpeta: hoy se lee HTML no documentado "
            "(Eduardo o administración de Google Workspace)",
            "units_per_master_box = 1 cuenta como empaque declarado, y 644 de "
            "los 9,926 poblados del catálogo activo valen exactamente 1 (la "
            "medición que cita inventario_maestro._cajas): «1 pieza por caja» "
            "casi siempre es el hueco, no una caja de una pieza (catálogo)",
        ],
    },
    "validado_bodega": {
        "titulo": "Validado bodega", "estado": "bloqueado", "filtrable": False,
        "depende": ("odoo",),
        "definicion": "Ubicación, stock, foto y specs en listo (los cuatro)",
        "fuente": "Odoo · stock.quant, product.product, imágenes",
        "motivo": "Bloqueado: specs no tiene definición, así que 4 de 4 da 0 por construcción",
        "desglose": [("recibido_y_3de4", "Cumple lo anterior (Recibido) con 3 de 4",
                      "recibido_y_3de4", ("kubera", "odoo"))],
        "falta": [
            "Specs: no existe la matriz por categoría ni el canal para capturarla "
            "(catálogo o contenido, con bodega)",
            "Foto de bodega para productos con variantes: el canal (Slack) no está "
            "construido (bodega)",
            "Decisión D2: ¿los hermanos por código base cuentan como variantes? El "
            "flujo usa la regla de la tabla (Eduardo con bodega)",
            "Decisión D4: ¿SCRAP, CUARENTENA y quants negativos cuentan como "
            "ubicación? Hoy sí (bodega)",
            "Decisión D5: «llegó contra packing list» no es requisito hoy; faltan "
            "las piezas esperadas por SKU y la tolerancia (Eduardo con bodega)",
            "Cajas contadas físicamente al recibir: no existen en ningún sistema "
            "(bodega)",
        ],
    },
    "bodega_3de4": {
        "titulo": "3 de 4 sin specs", "estado": "medido", "filtrable": True,
        "depende": ("odoo",),
        "definicion": "Ubicación, stock y foto en listo; specs no se toma en cuenta",
    },
    "listo_envio": {
        "titulo": "Listo para FULL o DROP", "estado": "bloqueado", "filtrable": False,
        "depende": ("kubera", "odoo"),
        "definicion": "Recibido y Validado bodega 4 de 4 (el costo no cuenta)",
        "fuente": "cruce en memoria de kubera y Odoo",
        "motivo": "Bloqueado: depende de Validado bodega 4 de 4, que es 0 mientras falten specs",
        "desglose": [("recibido_y_3de4", "Si specs no bloqueara (Recibido con 3 de 4)",
                      "recibido_y_3de4", ("kubera", "odoo"))],
        "falta": [
            "Specs (la etapa es 0 por construcción mientras falten)",
            "Criterio de a cuál de los dos va cada SKU, FULL o DROP: no se ha "
            "investigado si existe un campo que lo diga (KAM o Eduardo)",
        ],
    },
    "en_full": {
        "titulo": "En FULL", "estado": "medido", "filtrable": True,
        "depende": ("kubera",),
        "definicion": "Mercado Libre con stock_full > 0 (D3)",
        "fuente": "kubera · channel.listings (sync de 15 min)",
        "desglose": [
            ("cumple_3de4", "Cumple 3 de 4 de bodega", "en_full.cumple_3de4",
             ("kubera", "odoo")),
            ("fuera_de_odoo", "No existen en Odoo activo", "en_full.fuera_de_odoo",
             ("kubera", "odoo")),
        ],
        "falta": [
            "Decisión D3: una sola definición (stock_full de ML, is_fulfillment o "
            "FBA/WFS). La columna de la tabla todavía suma stock_full de todos los "
            "canales, así que puede no coincidir con esta tarjeta (Eduardo o KAM)",
            "Publicaciones con stock_full > 0 y status vacío: no se sabe si están "
            "vivas (quien publica en ML)",
            "SKUs en FULL que no existen en Odoo activo, casi todos códigos padre "
            "sin sufijo: no se cruzan con bodega (catálogo)",
        ],
    },
    "en_fba": {
        "titulo": "En FBA", "estado": "medido", "filtrable": True,
        "depende": ("kubera",),
        "definicion": "Amazon con stock_fba > 0 (su bodega, no es FULL)",
        "fuente": "kubera · channel.listings (sync de 15 min)",
        "desglose": [
            ("cumple_3de4", "Cumple 3 de 4 de bodega", "en_fba.cumple_3de4",
             ("kubera", "odoo")),
            ("fuera_de_odoo", "No existen en Odoo activo", "en_fba.fuera_de_odoo",
             ("kubera", "odoo")),
        ],
        "falta": [
            "Confirmar que `stock_fba` se sincroniza con la misma frecuencia que "
            "`stock_full`: si se queda viejo, la etapa envejece sin avisar (KAM)",
            "Walmart WFS no se puede contar por SKU: nadie escribe `is_fulfillment` "
            "ni stock de ese canal (las 235 filas vienen de `cargar_walmart.py`, "
            "a mano, sin tocarse desde el 17-ago), así que su segmento no se "
            "pinta. Las salidas a WFS sí se ven en FULLFILMENT (Eduardo o KAM)",
        ],
    },
    "en_drop": {
        "titulo": "En DROP", "estado": "medido", "filtrable": True,
        "depende": ("odoo_drop",),
        "definicion": "Existencias > 0 en ubicaciones internas del almacén DROP OFF",
        "fuente": "Odoo · stock.quant del almacén DROP (caché de 30 min)",
        "desglose": [("cumple_3de4", "Cumple 3 de 4 de bodega", "en_drop.cumple_3de4",
                      ("odoo_drop", "odoo"))],
        "falta": [
            "La lista cuenta también ubicaciones no vendibles del almacén; la "
            "columna «bodegas» de la fila solo cuenta las vendibles, así que un "
            "SKU puede estar aquí y no enseñar DROP en la fila (bodega)",
        ],
    },
    "restock": {
        "titulo": "Restock", "estado": "por_definir", "filtrable": False,
        "depende": (),
        "definicion": "Regla por definir",
        "fuente": "ninguna",
        "motivo": "Por definir: no hay regla ni cálculo; no se construye en esta versión",
        "falta": ["Regla de restock (base: la investigación del 14-sep)"],
    },
    "costo_validado": {
        "titulo": "Costo validado", "estado": "medido", "filtrable": True,
        "depende": ("kubera",),
        "definicion": ("Costo con candado: revisado_at no nulo. No es lo contrario "
                       "del chip «sin costo» de la tabla, que significa «sin renglón»"),
        "fuente": "kubera · costing.costos_validados.revisado_at",
        "desglose": [("cumple_3de4", "Cumple 3 de 4 de bodega",
                      "costo_validado.cumple_3de4", ("kubera", "odoo"))],
        "falta": [
            "Revisión humana: casi ninguna fila tiene revisado_at (equipo de costos)",
            "Medidas de caja y datos de costeo confiables: sin reverificar en "
            "esta pantalla (costos)",
        ],
    },
}

_ORDEN_ETAPAS = ("recibido", "validado_bodega", "listo_envio", "en_full",
                 "en_fba", "en_drop", "restock")


class _Contexto:
    """Qué fuentes se pueden creer AHORA (la antigüedad se juzga al leer)."""

    def __init__(self, foto: Foto, ahora: datetime, calentando: str | None = None):
        self.foto = foto
        self.calentando = calentando
        self.usable = {n: _utilizable(foto.fuentes.get(n), ahora) for n in FUENTES}

    def ok(self, deps: Iterable[str]) -> bool:
        return all(self.usable[d] for d in deps)

    def vieja(self, deps: Iterable[str]) -> bool:
        return any(self.foto.fuentes[d].vieja for d in deps if d in self.foto.fuentes)

    def motivo(self, deps: Iterable[str]) -> str:
        if self.calentando:
            return self.calentando
        partes = []
        for d in deps:
            if not self.usable[d]:
                f = self.foto.fuentes.get(d)
                err = (f.error if f and f.error
                       else "su último resultado bueno tiene más de 2 h")
                partes.append(f"sin dato de {d}: {err}")
        return "; ".join(partes)


def _etapa(clave: str, ctx: _Contexto) -> dict[str, Any]:
    m = _META[clave]
    deps = m["depende"]
    ok = ctx.ok(deps)
    por_definir = m["estado"] == "por_definir"
    n = None if por_definir or not ok else len(ctx.foto.listas.get(clave, ()))
    salida: dict[str, Any] = {
        "clave": clave,
        "titulo": m["titulo"],
        "n": n,
        "estado": m["estado"] if ok or por_definir else "sin_dato",
        "filtrable": m["filtrable"],
        "vieja": ctx.vieja(deps),
        "motivo": (m.get("motivo") if ok or por_definir else ctx.motivo(deps)),
        "definicion": m["definicion"],
        "fuente": m.get("fuente"),
        "falta": list(m.get("falta") or []),
    }
    if m.get("desglose"):
        salida["desglose"] = [
            {"clave": c, "titulo": t,
             "n": ctx.foto.cruces.get(llave) if ctx.ok(fuentes) else None}
            for c, t, llave, fuentes in m["desglose"]]
    return salida


def _validado_bodega(ctx: _Contexto) -> dict[str, Any]:
    salida = _etapa("validado_bodega", ctx)
    ok = ctx.ok(("odoo",))
    r = ctx.foto.requisitos
    medido = "medido" if ok else "sin_dato"
    salida["requisitos"] = [
        {"clave": "ubicacion", "titulo": "Ubicación", "estado": medido,
         "n": r.get("ubicacion") if ok else None,
         "definicion": ("Al menos un quant interno distinto de cero; incluye SCRAP, "
                        "CUARENTENA y negativos (D4)")},
        {"clave": "stock", "titulo": "Stock", "estado": medido,
         "n": r.get("stock") if ok else None,
         "definicion": "Piezas a la mano > 0 y disponibles > 0"},
        {"clave": "foto", "titulo": "Foto", "estado": medido,
         "n": r.get("foto") if ok else None,
         "en_espera": r.get("foto_espera") if ok else None,
         "definicion": ("Sin variantes: imagen en Odoo. Con variantes (plantilla o "
                        "código base, D2): espera la foto de bodega")},
        {"clave": "specs", "titulo": "Specs", "estado": "por_definir", "n": None,
         "definicion": "Matriz por categoría sin definir"},
    ]
    sub = _META["bodega_3de4"]
    salida["sub"] = {
        "clave": "bodega_3de4", "titulo": sub["titulo"],
        "n": len(ctx.foto.listas.get("bodega_3de4", ())) if ok else None,
        "estado": medido, "filtrable": sub["filtrable"],
        "definicion": sub["definicion"],
    }
    if ok and ctx.foto.universo_bodega == "odoo":
        salida["motivo"] = (f"{salida['motivo']}. Sin dato de kubera: se cuenta sobre "
                            f"los códigos de Odoo, no sobre core.products")
    return salida


def _fuente_json(f: Fuente) -> dict[str, Any]:
    return {"ok": f.ok, "vieja": f.vieja, "generado": _iso(f.generado),
            "ms": f.ms, "error": f.error, "sospechosa": f.sospechosa}


_FUENTE_SIN_DATO: Mapping[str, Any] = MappingProxyType(
    {"ok": False, "vieja": False, "generado": None, "ms": None,
     "error": None, "sospechosa": False})


def _respuesta(foto: Foto, ahora: datetime, calentando: str | None = None) -> dict[str, Any]:
    ctx = _Contexto(foto, ahora, calentando)
    etapas = [_validado_bodega(ctx) if c == "validado_bodega" else _etapa(c, ctx)
              for c in _ORDEN_ETAPAS]
    hay_foto = calentando is None
    # SOLO las tres del catálogo: `canales` es de los conteos por canal y una
    # lectura suya vieja no puede volver ámbar una respuesta que no la usa.
    barra = [foto.fuentes[n] for n in FUENTES_BARRA if n in foto.fuentes]
    return {
        "estado": ("calentando" if not hay_foto
                   else "vieja" if any(f.vieja for f in barra)
                   else "listo"),
        "motivo": calentando,
        "generado": _iso(foto.generado) if hay_foto else None,
        "edad_s": int((ahora - foto.generado).total_seconds()) if hay_foto else None,
        "ttl_s": TTL_S,
        "universo": {
            "fuente": "core.products",
            "n": foto.universo_n if ctx.usable["kubera"] else None,
            "definicion": "D6: todo core.products, el único conjunto que contiene a los demás",
        },
        "fuentes": {n: _fuente_json(foto.fuentes[n])
                    for n in FUENTES_BARRA if n in foto.fuentes},
        "etapas": etapas,
        "carril": _etapa("costo_validado", ctx),
    }


def _foto_vacia(ahora: datetime) -> Foto:
    nada = Fuente(ok=False, vieja=False, generado=None, ms=None, error=None, datos=None)
    return Foto(generado=ahora, fuentes=MappingProxyType({n: nada for n in FUENTES}),
                listas=MappingProxyType({}), requisitos=MappingProxyType({}),
                cruces=MappingProxyType({}), universo_n=None, universo_bodega="")


# ─────────────────────────────────────────────────────────────────────────────
# LO QUE LEEN LOS ENDPOINTS (solo memoria; nunca esperan a la red)
# ─────────────────────────────────────────────────────────────────────────────

_foto: Foto | None = None


def conteos() -> dict[str, Any]:
    """La respuesta de `GET /api/inventario/flujo`. Si no hay foto o venció,
    lanza el armado en su hilo y contesta lo que tenga."""
    foto = _foto
    ahora = _ahora()
    calentar_en_fondo(motivo="peticion")
    if foto is None:
        return _respuesta(_foto_vacia(ahora), ahora, calentando=_motivo_calentando())
    return _respuesta(foto, ahora)


def skus_de_etapa(etapa: str, page: int = 1, per_page: int = 40,
                  q: str | None = None) -> dict[str, Any]:
    """La respuesta de `GET /api/inventario/flujo/skus`: una página de la lista
    de la foto, por SKU ascendente. `q` busca subcadena del SKU sin distinguir
    mayúsculas. El router ya validó rangos; aquí se acota igual por si acaso."""
    if etapa not in ETAPAS_FILTRABLES:
        raise ValueError(f"{etapa}: etapa desconocida o por definir")
    page = max(1, int(page))
    per_page = min(200, max(1, int(per_page)))
    aguja = (q or "").strip() or None
    foto = _foto
    ahora = _ahora()
    calentar_en_fondo(motivo="peticion")
    base: dict[str, Any] = {"etapa": etapa, "page": page, "per_page": per_page,
                            "q": aguja}
    if foto is None:
        return {**base, "estado": "calentando", "motivo": _motivo_calentando(),
                "generado": None, "edad_s": None, "total": None,
                "total_pages": 0, "skus": []}
    ctx = _Contexto(foto, ahora)
    deps = _META[etapa]["depende"]
    comun = {**base, "generado": _iso(foto.generado),
             "edad_s": int((ahora - foto.generado).total_seconds())}
    if not ctx.ok(deps):
        return {**comun, "estado": "sin_dato", "motivo": ctx.motivo(deps),
                "total": None, "total_pages": 0, "skus": []}
    lista: tuple[str, ...] | list[str] = foto.listas.get(etapa, ())
    if aguja:
        a = aguja.casefold()
        lista = [s for s in lista if a in s.casefold()]
    total = len(lista)
    inicio = (page - 1) * per_page
    return {**comun, "estado": "vieja" if ctx.vieja(deps) else "listo",
            "motivo": None, "total": total,
            "total_pages": math.ceil(total / per_page),
            "skus": list(lista[inicio:inicio + per_page])}


# ═════════════════════════════════════════════════════════════════════════════
# LO QUE LEE /omnicanal (el stepper, el filtro por etapa y el sello por fila)
#
# Todo lo de aquí abajo es MEMORIA: se toma `_foto` UNA vez por petición y no se
# vuelve a mirar, para que las 40 tarjetas, el total y los conteos vengan de la
# MISMA foto. Nada llama a `refrescar` (12–35 s); como mucho a
# `calentar_en_fondo`, que solo lanza un hilo.
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class FotoPeticion:
    """La foto tal como la ve UNA petición, con su estado ya juzgado."""
    foto: Foto | None
    ctx: Any                       # `_Contexto` o None
    estado: str                    # apagado | calentando | vieja | listo
    motivo: str | None
    generado: str | None

    @property
    def usable(self) -> bool:
        return self.foto is not None and self.foto.indice is not None


FOTO_APAGADA = FotoPeticion(
    foto=None, ctx=None, estado="apagado",
    motivo=("INVENTARIO_FLUJO_ENABLED=false: la foto del catálogo no se arma "
            "en este ambiente"),
    generado=None)


def foto_para_peticion() -> FotoPeticion:
    """La foto para esta petición. Con el flag apagado devuelve una CONSTANTE
    sin tocar `_foto` ni lanzar hilos: es la ruta caliente del panel entero y
    producción viene con el flag en false."""
    if not settings.inventario_flujo_enabled:
        return FOTO_APAGADA
    foto = _foto
    ahora = _ahora()
    calentar_en_fondo(motivo="omnicanal")
    if foto is None:
        return FotoPeticion(None, None, "calentando", _motivo_calentando(), None)
    ctx = _Contexto(foto, ahora)
    vieja = any(foto.fuentes[n].vieja for n in FUENTES_BARRA if n in foto.fuentes)
    return FotoPeticion(foto, ctx, "vieja" if vieja else "listo", None,
                        _iso(foto.generado))


@dataclass(frozen=True)
class ListaEtapa:
    estado: str                    # apagado | calentando | sin_dato | vieja | listo
    motivo: str | None
    skus_mayus: frozenset[str]
    generado: str | None
    vieja: bool


def lista_etapa(fp: FotoPeticion, etapa: str, *,
                requiere_kubera: bool = False) -> ListaEtapa:
    """Los SKUs de una etapa EN MAYÚSCULAS, o el motivo por el que no hay lista.

    `requiere_kubera` lo pide General: ahí la etapa se aplica por `wc_id`, que
    solo existe en `core.products`. Sin kubera la lista existiría pero no se
    podría traducir a productos de Woo, y filtrar con la mitad del puente daría
    de menos sin decirlo."""
    if fp.estado in ("apagado", "calentando"):
        return ListaEtapa(fp.estado, fp.motivo, frozenset(), None, False)
    deps = tuple(_META[etapa]["depende"]) + (("kubera",) if requiere_kubera else ())
    ctx = fp.ctx
    if not ctx.ok(deps):
        return ListaEtapa("sin_dato", ctx.motivo(deps), frozenset(),
                          fp.generado, False)
    idx = fp.foto.indice
    return ListaEtapa("vieja" if ctx.vieja(deps) else "listo", None,
                      idx.etapas_mayus.get(etapa, frozenset()),
                      fp.generado, ctx.vieja(deps))


def expandir_padres(fp: FotoPeticion, terminos: Iterable[str]) -> set[str]:
    """Los términos EN MAYÚSCULAS más, por cada uno que sea el SKU de un PADRE,
    los SKUs de sus variantes.

    Sin esto, escribir el SKU del padre con una etapa puesta da 0: la etapa vive
    en las variantes (el padre nunca se costea ni llega en el packing list) y la
    intersección es exacta."""
    salida = {(t or "").strip().upper() for t in terminos if (t or "").strip()}
    if not fp.usable:
        return salida
    idx = fp.foto.indice
    for u in list(salida):
        wc = idx.wc_mayus.get(u)
        if wc and wc[0] in idx.padres_wc:
            salida.update(h.upper() for h in idx.hijos_por_wc_padre.get(wc[0], ()))
    return salida


def ids_woo(fp: FotoPeticion, skus_mayus: Iterable[str]) -> tuple[list[int], list[int]]:
    """`(ids de PRODUCTO, ids de FILA)` de WooCommerce para esos SKUs.

    Los de producto colapsan la variante a su padre —que es lo que pagina el
    listado anidado— y los de fila son el `wc_id` propio de cada SKU, que es lo
    que pagina el aplanado. Van por `p.ID IN`, la PK: evita las 17 consultas en
    serie de `skus_padre` y el recorrido de `meta_value` con 13.5k comodines."""
    if not fp.usable:
        return [], []
    idx = fp.foto.indice
    productos: set[int] = set()
    filas: set[int] = set()
    for u in skus_mayus:
        wc = idx.wc_mayus.get(u)
        if not wc:
            continue
        wc_id, padre = wc
        filas.add(wc_id)
        productos.add(padre or wc_id)
    return sorted(productos), sorted(filas)


# ── EL SELLO POR FILA ────────────────────────────────────────────────────────

_SKU_SINTETICO = re.compile(r"^WC-\d+$", re.IGNORECASE)

_ETAPA_TEXTO = {
    "en_full_y_drop": "En FULL y DROP",
    "en_full": "En FULL",
    "en_fba": "En FBA",
    "en_drop": "En DROP",
    "listo_envio": "Listo para FULL o DROP",
    "validado_bodega": "Validado bodega",
    "bodega_3de4": "Bodega 3 de 4 (sin specs)",
    "recibido": "Recibido (aprox.)",
    "ninguna": "Sin etapa del flujo",
    "sin_dato": "Sin dato del flujo",
}

# En FULL es `stock_full > 0` de MERCADO LIBRE (D3). En las otras pestañas la
# etiqueta lleva «(ML)» o se leería como FBA o WFS, que no son lo mismo.
_CANALES_FULL_PROPIO = ("general", "mercado_libre")

_TITULOS_VARIANTES = (("en_full", "En FULL"), ("en_fba", "En FBA"),
                      ("en_drop", "En DROP"),
                      ("bodega_3de4", "3 de 4"), ("recibido", "Recibido"),
                      ("sin_dato", "sin dato"))


def texto_variantes(v: Mapping[str, int] | None) -> str:
    """«1 En FULL · 2 Recibido de 5». Los ceros NO se escriben: un resumen que
    dice «0 En DROP» gasta la línea en lo que no pasó."""
    if not v:
        return ""
    total = int(v.get("total") or 0)
    partes = [f"{v[c]} {t}" for c, t in _TITULOS_VARIANTES if v.get(c)]
    if not partes:
        return f"{total} sin etapa"
    return " · ".join(partes) + f" de {total}"


def resumen_variantes(sellos: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Cuántas variantes hay en cada etapa, por PERTENENCIA y no por la etapa
    resuelta: una variante En FULL que además cumple 3 de 4 cuenta en las dos.
    Así el resumen del padre dice lo mismo que el stepper."""
    salida = {"total": 0, "recibido": 0, "bodega_3de4": 0, "en_full": 0,
              "en_fba": 0, "en_drop": 0, "sin_dato": 0}
    for s in sellos:
        if not s:
            continue
        salida["total"] += 1
        pasos = s.get("pasos") or {}
        bod = pasos.get("bodega") or {}
        dest = pasos.get("destino") or {}
        if (pasos.get("recibido") or {}).get("estado") == "si":
            salida["recibido"] += 1
        # `bodega_3de4` es pertenencia a la lista de la foto, que es sensible a
        # mayúsculas: tres cuadros en listo con el SKU FUERA de la lista es
        # justo lo que marca `escritura_distinta`.
        tres = all(bod.get(c) == "listo" for c in ("ubicacion", "stock", "foto"))
        if tres and not bod.get("escritura_distinta"):
            salida["bodega_3de4"] += 1
        if dest.get("full") is True:
            salida["en_full"] += 1
        if dest.get("fba") is True:
            salida["en_fba"] += 1
        if dest.get("drop") is True:
            salida["en_drop"] += 1
        if s.get("etapa") == "sin_dato":
            salida["sin_dato"] += 1
    return salida


def _codigo_odoo(sku: str, canon: str | None, o: DatosOdoo | None,
                 idx: Indice) -> tuple[str | None, bool]:
    """`(código de Odoo, ambiguo)`. La tabla busca `default_code` EXACTO, así que
    aquí se resuelve igual: el SKU tal cual, si no la escritura de kubera, y si
    no el ÚNICO código de Odoo que coincide sin mayúsculas. Con más de uno NO se
    adivina."""
    if o is None:
        return None, False
    if sku in o.estados:
        return sku, False
    if canon and canon in o.estados:
        return canon, False
    candidatos = idx.odoo_mayus.get(sku.upper(), ())
    if len(candidatos) == 1:
        return candidatos[0], False
    if len(candidatos) > 1:
        return None, True
    return None, False


def sello(fp: FotoPeticion, sku: str, *, canal: str = "general",
          _profundidad: int = 0) -> dict[str, Any] | None:
    """El flujo de UN SKU, listo para pintar. PURO y solo memoria.

    `None` cuando no hay nada honesto que decir: la foto apagada o calentando, y
    los SKUs sintéticos `WC-<id>` que Woo inventa para productos sin SKU — ésos
    no existen en Odoo ni en costos, y un sello diría «no existe en Odoo, sin
    renglón» de algo que ni siquiera es un SKU."""
    if fp.estado in ("apagado", "calentando") or not fp.usable:
        return None
    sku = (sku or "").strip()
    if not sku or _SKU_SINTETICO.match(sku):
        return None

    foto, ctx, idx = fp.foto, fp.ctx, fp.foto.indice
    k: DatosKubera | None = foto.fuentes["kubera"].datos if ctx.usable["kubera"] else None
    o: DatosOdoo | None = foto.fuentes["odoo"].datos if ctx.usable["odoo"] else None
    d: DatosDrop | None = foto.fuentes["odoo_drop"].datos if ctx.usable["odoo_drop"] else None
    c: DatosCanales | None = idx.canales if ctx.usable["canales"] else None
    mayus = sku.upper()
    canon = idx.kub_mayus.get(mayus)
    en_catalogo = None if k is None else canon is not None

    vieja_k = foto.fuentes["kubera"].vieja
    gen_k = _iso(foto.fuentes["kubera"].generado)
    vieja_o = foto.fuentes["odoo"].vieja
    gen_o = _iso(foto.fuentes["odoo"].generado)

    # ── PADRE ────────────────────────────────────────────────────────────────
    # Un código que es `wc_parent_id` de otros no se costea ni llega en el
    # packing list: juzgarlo con las reglas normales diría «no existe en Odoo,
    # sin renglón» de 1,500 productos. Lleva el resumen de sus variantes.
    wc = idx.wc_mayus.get(mayus) if canon else None
    es_padre = bool(wc and wc[0] in idx.padres_wc and _profundidad == 0)
    variantes = None
    if es_padre:
        hijos = idx.hijos_por_wc_padre.get(wc[0], ())
        variantes = resumen_variantes(
            [sello(fp, h, canal=canal, _profundidad=1) for h in hijos])

    # ── RECIBIDO ─────────────────────────────────────────────────────────────
    # El código de Odoo se resuelve ANTES que Recibido porque desde el 18-sep la
    # etapa también se contesta con el empaque master, que es un campo de
    # `product.product`: la misma resolución por `default_code` exacto que usa
    # Bodega más abajo, hecha una sola vez.
    codigo, ambiguo = _codigo_odoo(sku, canon, o, idx)

    # Las DOS mitades por separado. `None` es «ya no sé», nunca «no»:
    #   · `pl`  — sin kubera no se sabe si hay cajas en el packing list.
    #   · `emp` — sin Odoo, o con el código ambiguo (el mismo SKU escrito de dos
    #     formas), no se sabe de qué producto leer el empaque. Que el SKU NO
    #     exista en Odoo sí se sabe, y entonces es `False`, no `None`.
    pl = None if k is None else (canon is not None and canon in k.recibido)
    emp = (None if (o is None or ambiguo)
           else (codigo is not None and codigo in o.empaque))

    # La frescura del paso es la de la MÁS VIEJA de las dos fuentes que lo
    # contestan (`destino` ya hacía lo mismo con kubera y DROP): un Odoo de hace
    # tres horas envejece la cifra aunque kubera esté al día, y enseñar la fecha
    # de kubera a secas diría que el dato es más fresco de lo que es.
    vieja_r = bool(vieja_k or vieja_o)
    gen_r = min([g for g in (gen_k, gen_o) if g], default=None)

    if es_padre:
        # Un padre ni se costea ni se empaca: las que llegan son sus variantes.
        recibido = {"estado": "na", "motivo": None, "fuente": None,
                    "vieja": vieja_r, "generado": gen_r}
    elif pl or emp:
        # Basta UNA: es una unión. `fuente` dice cuál contestó, que es lo que
        # separa «viene en el packing list del embarque» de «Odoo sabe cómo se
        # empaca» — dos evidencias distintas del mismo casillero, y ninguna de
        # las dos afirma que la mercancía entró a la bodega.
        recibido = {"estado": "si", "motivo": None,
                    "fuente": ("ambas" if (pl and emp)
                               else "packing_list" if pl else "odoo"),
                    "vieja": vieja_r, "generado": gen_r}
    elif pl is None or emp is None:
        # Una mitad dice «no» y la otra «no sé»: el SKU podría estar en la que
        # falta, así que el casillero entero es sin dato. Es la lección de los
        # 964 pedidos fantasma aplicada a media regla.
        recibido = {"estado": "sin_dato", "motivo": None, "fuente": None,
                    "vieja": vieja_r, "generado": gen_r}
    else:
        # Ninguna de las dos. El motivo sigue describiendo el lado del packing
        # list, que es el accionable y el único que distingue dos huecos («no
        # tiene renglón en costos» contra «tiene renglón y viene en cero»); el
        # lado de Odoo no tiene grados y se nombra entero en `le_falta`.
        motivo = ("sin_renglon" if canon is None or canon not in k.con_renglon
                  else "sin_cajas")
        recibido = {"estado": "no", "motivo": motivo, "fuente": None,
                    "vieja": vieja_r, "generado": gen_r}

    # ── BODEGA ───────────────────────────────────────────────────────────────
    specs = inv.estado_specs() if o is not None else "sin_dato"
    if es_padre:
        bodega = {"ubicacion": "na", "stock": "na", "foto": "na", "specs": "na",
                  "n_listo": None, "en_odoo": None, "archivado": None,
                  "codigo_odoo": None, "escritura_distinta": False,
                  "vieja": vieja_o, "generado": gen_o}
    elif o is None or ambiguo:
        bodega = {"ubicacion": "sin_dato", "stock": "sin_dato", "foto": "sin_dato",
                  "specs": "sin_dato" if o is None else specs, "n_listo": None,
                  "en_odoo": None if o is None else False if not ambiguo else None,
                  "archivado": None, "codigo_odoo": None,
                  "escritura_distinta": False, "vieja": vieja_o, "generado": gen_o}
    else:
        if codigo is not None:
            ubic, stock, foto_e = o.estados[codigo]
            en_odoo = True
            archivado = codigo not in o.activos
        else:
            # Fuera de Odoo: los tres en «falta» y se dice, que NO es lo mismo
            # que «0 de 4» — casi todos son códigos padre sin sufijo.
            ubic = stock = foto_e = "falta"
            en_odoo = False
            archivado = None
        cuadros = (ubic, stock, foto_e, specs)
        tres_listo = ubic == stock == foto_e == "listo"
        bodega = {
            "ubicacion": ubic, "stock": stock, "foto": foto_e, "specs": specs,
            "n_listo": sum(1 for x in cuadros if x == "listo"),
            "en_odoo": en_odoo, "archivado": archivado, "codigo_odoo": codigo,
            # La lista de la foto es una intersección SENSIBLE a mayúsculas; si
            # los cuadros dicen tres listos y el SKU no está en ella, la
            # explicación es que Odoo lo escribe distinto. Se dice en vez de
            # contradecir al filtro y al conteo.
            #
            # `en_catalogo is not False` es la OTRA explicación posible, y no es
            # de escritura: `bodega_3de4` se cruza contra `k.universo`
            # (armar_foto), así que un SKU que no está en core.products queda
            # fuera de la lista por más idéntico que esté escrito en los dos
            # lados. Acusarlo mandaría a alguien a corregir un nombre que ya
            # coincide y taparía el hueco real, que es el del seam.
            "escritura_distinta": bool(
                tres_listo and en_catalogo is not False
                and mayus not in idx.etapas_mayus.get("bodega_3de4", frozenset())),
            "vieja": vieja_o, "generado": gen_o,
        }

    # ── LISTO PARA FULL O DROP ───────────────────────────────────────────────
    if es_padre:
        listo = {"estado": "na"}
    elif specs != "listo":
        listo = {"estado": "bloqueado"}
    elif k is None or o is None:
        listo = {"estado": "sin_dato"}
    elif recibido["estado"] == "si" and bodega["n_listo"] == 4:
        listo = {"estado": "si"}
    else:
        listo = {"estado": "no"}

    # ── DESTINO ──────────────────────────────────────────────────────────────
    full = None if k is None else (canon is not None and canon in k.en_full)
    fba = None if k is None else (canon is not None and canon in k.en_fba)
    drop = None if d is None else mayus in d.skus
    cuentas = None if c is None else list(c.full_cuentas.get(mayus, ()))
    destino = {
        "full": full, "fba": fba, "drop": drop, "full_cuentas": cuentas,
        "sin_dato": not (full is True or drop is True) and (full is None or drop is None),
        "vieja": bool(foto.fuentes["kubera"].vieja or foto.fuentes["odoo_drop"].vieja),
    }

    # ── ETAPA ────────────────────────────────────────────────────────────────
    tres_de_4 = mayus in idx.etapas_mayus.get("bodega_3de4", frozenset())
    if es_padre:
        etapa = "padre"
    elif full and drop:
        etapa = "en_full_y_drop"
    elif full:
        etapa = "en_full"
    elif fba:
        # Debajo de FULL a propósito: un SKU en las dos bodegas se lee como «En
        # FULL» en cualquier pestaña, que es la que mueve más piezas.
        etapa = "en_fba"
    elif drop:
        etapa = "en_drop"
    elif listo["estado"] == "si":
        etapa = "listo_envio"
    elif bodega["n_listo"] == 4:
        etapa = "validado_bodega"
    elif tres_de_4:
        etapa = "bodega_3de4"
    elif recibido["estado"] == "si":
        etapa = "recibido"
    elif k is not None and o is not None and d is not None:
        # «No hay» solo se puede afirmar con las tres fuentes: un None de una
        # fuente caída no significa «no», significa «ya no sé».
        etapa = "ninguna"
    else:
        etapa = "sin_dato"

    if etapa == "padre":
        texto = "Padre · " + texto_variantes(variantes)
    else:
        texto = _ETAPA_TEXTO[etapa]
        if etapa in ("en_full", "en_full_y_drop") and canal not in _CANALES_FULL_PROPIO:
            texto = texto.replace("En FULL", "En FULL (ML)")
        if etapa == "en_fba" and canal != "amazon":
            # Fuera de Amazon, «En FBA» a secas se leería como bodega de ESTE
            # canal; el sufijo dice de quién es.
            texto = "En FBA (Amazon)"
        if BODEGA_DEL_CANAL.get(canal) == "en_fba" and fba:
            # En la pestaña de Amazon manda SU bodega: un SKU que está en las
            # dos decía «En FULL (ML)» mientras el filtro activo era FBA, y la
            # fila parecía de otra etapa. La otra no se esconde, se menciona.
            texto = "En FBA" + (" · también FULL (ML)" if full else "")
            if drop:
                texto += " y DROP"
    if destino["sin_dato"]:
        texto += " · destino sin dato"

    # ── LE FALTA (el camino a Listo; destino, costo y restock nunca entran) ──
    le_falta: list[str] = []
    if not es_padre:
        if recibido["estado"] == "sin_dato" and pl is None:
            le_falta.append("sin dato de kubera")
        # El otro sin_dato de Recibido es el de Odoo (`emp is None`), y ahí NO
        # se escribe nada: el renglón de bodega de más abajo ya dice «sin dato
        # de Odoo» por la misma caída, y repetirlo mandaría a revisar dos cosas
        # donde solo hay una.
        elif recibido["estado"] == "no":
            # Se nombran LAS DOS mitades: con la regla nueva un «Recibido (sin
            # cajas en costos)» a secas haría pensar que llenando el packing
            # list se resuelve, cuando declarar el empaque en Odoo también basta.
            le_falta.append(
                "Recibido (no tiene renglón en costos ni empaque en Odoo)"
                if recibido["motivo"] == "sin_renglon"
                else "Recibido (sin cajas en costos ni empaque en Odoo)")
        if bodega["ubicacion"] == "sin_dato":
            le_falta.append("sin dato de Odoo")
        elif bodega["en_odoo"] is False:
            le_falta.append("no existe en Odoo")
        else:
            if bodega["ubicacion"] != "listo":
                le_falta.append("ubicación")
            if bodega["stock"] != "listo":
                le_falta.append("stock")
            if bodega["foto"] == "falta":
                le_falta.append("foto")
            elif bodega["foto"] != "listo":
                le_falta.append("foto de bodega")
        if bodega["escritura_distinta"]:
            le_falta.append(f"SKU escrito distinto en Odoo ({bodega['codigo_odoo']})")
        elif (en_catalogo is False
                and bodega["ubicacion"] == bodega["stock"] == bodega["foto"] == "listo"):
            # Bodega lo tiene todo y aun así no entra en «3 de 4»: lo que falta
            # no está en Odoo sino en kubera, y se nombra en vez de dejar el
            # renglón de Recibido cargando solo con la explicación.
            le_falta.append("no está en el catálogo de kubera")
        if bodega["specs"] != "listo" and bodega["specs"] != "sin_dato":
            le_falta.append("specs")

    return {
        "etapa": etapa,
        "etapa_texto": texto,
        "le_falta": le_falta,
        "en_catalogo": en_catalogo,
        "pasos": {"recibido": recibido, "bodega": bodega, "listo": listo,
                  "destino": destino, "restock": {"estado": "por_definir"}},
        "variantes": variantes,
    }


# ── LOS CONTEOS DEL STEPPER ──────────────────────────────────────────────────

# El orden de los segmentos. `bodega_3de4` viaja aparte de `validado_bodega`
# porque es la que se puede pulsar: la de 4 de 4 está bloqueada por specs.
_ORDEN_CANAL = ("recibido", "bodega_3de4", "validado_bodega", "listo_envio",
                "en_full", "en_drop", "restock")


def _orden_de(canal: str) -> tuple[str, ...]:
    """El mismo orden, con LA bodega de este canal — o sin ninguna.

    `en_full` es el hueco del molde: se sustituye por la etapa que el canal sí
    tiene (FBA en Amazon) y se quita donde no hay ninguna."""
    bodega = BODEGA_DEL_CANAL.get(canal)
    if bodega == "en_full":
        return _ORDEN_CANAL
    return tuple(e for e in _ORDEN_CANAL if e != "en_full"
                 ) if bodega is None else tuple(
        bodega if e == "en_full" else e for e in _ORDEN_CANAL)


def _criterio_efectivo(canal: str, criterio: str,
                       c: DatosCanales | None) -> str:
    """El criterio que de verdad se aplicó. `activas` se degrada a `todas`
    donde el canal no sabe contestar «¿se puede comprar hoy?», y `publicados`
    también en Temu, cuya rejilla no filtra por ese botón."""
    if criterio == "activas" and (c is None or canal in c.sin_activas):
        return "todas"
    if criterio == "publicados" and canal == "temu":
        return "todas"
    return criterio


def _conteos_canal(foto: Foto | None, ahora: datetime, *, canal: str,
                   cuenta: str | None, criterio: str, aplanado: bool,
                   lee_publicaciones: bool, flag: bool) -> dict[str, Any]:
    """El cuerpo de `GET /api/inventario/flujo/canal`. PURO.

    EL NÚMERO Y EL CLIC SE SEPARAN. Una caída de `channel.listings` deja las
    cifras en `null` y el filtro PULSABLE: la etapa se sigue pudiendo aplicar
    porque sale de la foto, y apagar el botón por no saber contar sería castigar
    al usuario por una falla que no le impide nada."""
    es_general = canal == "general"
    # Con el flag apagado la foto de memoria (si quedara una de antes de
    # apagarlo) NO se usa: el modo legado no enseña cifras del flujo.
    if not flag:
        foto = None
    ctx = _Contexto(foto, ahora) if foto is not None else None
    idx = foto.indice if foto is not None else None
    c: DatosCanales | None = (idx.canales if idx is not None
                              and ctx is not None and ctx.usable["canales"] else None)

    # ¿Puede este canal filtrar EXACTO? Es lo único que decide el clic.
    if canal == "shein":
        puede_filtrar, por_que = False, "Shein se pinta con datos de ejemplo: no filtra por etapa."
    elif canal in ("mercado_libre", "amazon") and not lee_publicaciones:
        puede_filtrar, por_que = False, ("La rejilla lee MySQL "
                                         "(SUPABASE_READ_PUBLICACIONES apagado) y no "
                                         "puede filtrar exacto.")
    else:
        puede_filtrar, por_que = True, None

    hay_foto = foto is not None and idx is not None
    if not flag:
        estado, motivo = "apagado", FOTO_APAGADA.motivo
    elif not hay_foto:
        estado, motivo = "calentando", _motivo_calentando()
    elif not puede_filtrar:
        estado, motivo = "sin_dato", por_que
    else:
        estado, motivo = "listo", None

    cuenta = (cuenta or "").strip() or None
    llave_cuenta = cuenta if (canal == "mercado_libre" and cuenta) else "*"
    efectivo = _criterio_efectivo(canal, criterio, c)
    unidad = ("fila_woo" if aplanado else "producto_woo") if es_general else "publicacion"

    # El conjunto de la pestaña: { SKU_MAYÚS: filas }. En General no hay
    # publicaciones que contar — la rejilla lista productos de Woo — y el puente
    # es `wc_id`, así que ahí el número depende de kubera y no de canales.
    S: Mapping[str, int] | None = None
    if hay_foto and not es_general and c is not None:
        S = c.publicaciones.get((canal, llave_cuenta, efectivo), _VACIO)

    def _deps_numero(deps: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(deps) + (("kubera",) if es_general else ("canales",))

    def _motivo_numero(deps: tuple[str, ...]) -> str:
        if es_general and (ctx is None or not ctx.usable["kubera"]):
            return "sin dato de kubera: no hay wc_id"
        if not es_general and (ctx is None or not ctx.usable["canales"]):
            f = foto.fuentes.get("canales") if foto is not None else None
            err = (f.error if f is not None and f.error
                   else "su último resultado bueno tiene más de 2 h")
            return f"sin conteo por canal: {err}"
        return ctx.motivo(deps) if ctx is not None else "sin foto"

    def _n_de(skus: frozenset[str]) -> int:
        if es_general:
            vistos: set[int] = set()
            for u in skus:
                wc = idx.wc_mayus.get(u)
                if not wc:
                    continue
                wc_id, padre = wc
                if aplanado:
                    # Con el aplanado la fila es la variante y el padre
                    # desaparece: contarlo sumaría una fila que no existe.
                    if wc_id in idx.padres_wc:
                        continue
                    vistos.add(wc_id)
                else:
                    vistos.add(padre or wc_id)
            return len(vistos)
        return sum(S.get(u, 0) for u in skus) if S is not None else 0

    def _entrada(clave: str) -> dict[str, Any]:
        m = _META[clave]
        deps = tuple(m["depende"])
        por_definir = m["estado"] == "por_definir"
        bloqueada = m["estado"] == "bloqueado"
        etapas_mayus = idx.etapas_mayus if idx is not None else {}
        skus = etapas_mayus.get(clave, frozenset())

        # ── el NÚMERO ──
        n: int | None = None
        n_motivo: str | None = None
        if not puede_filtrar:
            # El canal no sabe contestar la pregunta: una cifra sacada de la
            # foto sería un número que la lista no puede reproducir.
            n_motivo = por_que
        elif not hay_foto:
            n_motivo = motivo
        elif por_definir:
            n_motivo = m.get("motivo")
        elif not ctx.ok(_deps_numero(deps)):
            n_motivo = _motivo_numero(_deps_numero(deps))
        elif bloqueada:
            # `validado_bodega` cuenta de verdad (y da 0 por construcción);
            # `listo_envio` no tiene cifra hasta que specs exista.
            n = _n_de(skus) if clave == "validado_bodega" else None
            n_motivo = m.get("motivo")
        else:
            n = _n_de(skus)

        # ── el CLIC ──
        if not puede_filtrar:
            clicable = False
        elif clave in ("en_drop", "costo_validado"):
            # Ninguna de las dos necesita la foto: En DROP se lee en vivo de
            # Odoo y Costo validado sale de costing. Con el flag apagado son lo
            # único que se puede pulsar, que es justo el modo legado.
            clicable = True
        elif not m["filtrable"]:
            clicable = False
        else:
            deps_clic = deps + (("kubera",) if es_general else ())
            clicable = bool(flag and hay_foto and ctx.ok(deps_clic))

        estado_e = m["estado"]
        if not hay_foto or (not por_definir and not (ctx and ctx.ok(deps))):
            estado_e = "sin_dato"
        salida = {
            "clave": clave, "titulo": m["titulo"], "n": n, "estado": estado_e,
            "filtrable": m["filtrable"], "clicable": clicable,
            "vieja": bool(ctx.vieja(_deps_numero(deps))) if hay_foto else False,
            "motivo": (m.get("motivo") if estado_e != "sin_dato"
                       else (motivo if not hay_foto else ctx.motivo(deps))),
            "n_motivo": n_motivo,
        }
        if clave == "listo_envio":
            # «Si specs no bloqueara»: Recibido ∩ 3 de 4 en ESTA pestaña.
            salida["n_sin_specs"] = (
                _n_de(etapas_mayus.get("recibido_y_3de4", frozenset()))
                if hay_foto and ctx.ok(_deps_numero(("kubera", "odoo"))) else None)
        return salida

    etapas = [_entrada(c_) for c_ in _orden_de(canal)]
    carril = _entrada("costo_validado")
    carril["param"] = "revisado"

    total: int | None = None
    if hay_foto and puede_filtrar and not es_general and S is not None:
        total = sum(S.values())
    if hay_foto and estado == "listo":
        deps_vivas = [e for e in etapas + [carril] if e["vieja"]]
        if deps_vivas:
            estado = "vieja"

    return {
        "canal": canal, "cuenta": cuenta, "criterio": criterio,
        "criterio_efectivo": efectivo,
        "unidad": unidad,
        # En General el número sale de `core.products` y los huecos del seam
        # (productos de Woo sin fila) no se pueden contar: se declara «≈».
        "unidad_aprox": es_general,
        "estado": estado, "motivo": motivo,
        "generado": _iso(foto.generado) if hay_foto else None,
        "edad_s": int((ahora - foto.generado).total_seconds()) if hay_foto else None,
        "ttl_s": TTL_S,
        # Las CUATRO siempre, también sin foto: quien pinta los puntitos del
        # rótulo espera las cuatro llaves, y un diccionario vacío lo obligaría
        # a distinguir «no hay foto» de «no hay fuente».
        "fuentes": {n: (_fuente_json(foto.fuentes[n])
                        if foto is not None and n in foto.fuentes
                        else _FUENTE_SIN_DATO)
                    for n in FUENTES},
        "total": total,
        "etapas": etapas,
        "carril": carril,
        "catalogo": {
            # Las DOS fuentes: desde el 18-sep Recibido es la unión del packing
            # list y el empaque de Odoo, y con Odoo caído la lista de la foto se
            # queda vacía a propósito. Mirar solo `kubera` aquí pintaría ese
            # vacío como un 0 del catálogo entero.
            "recibido": (len(idx.etapas_mayus.get("recibido", ()))
                         if hay_foto and ctx.ok(_META["recibido"]["depende"])
                         else None),
            "recibido_fuera_de_odoo": foto.cruces.get("recibido.fuera_de_odoo") if hay_foto else None,
            "recibido_y_3de4": foto.cruces.get("recibido_y_3de4") if hay_foto else None,
        },
    }


def conteos_canal(*, canal: str, cuenta: str | None = None,
                  criterio: str = "todas",
                  aplanar: bool | None = None) -> dict[str, Any]:
    """La respuesta de `GET /api/inventario/flujo/canal`. Solo memoria."""
    foto = _foto
    ahora = _ahora()
    flag = bool(settings.inventario_flujo_enabled)
    if flag:
        calentar_en_fondo(motivo="omnicanal")
    else:
        foto = None
    aplanado = bool(settings.listado_aplanado) if aplanar is None else bool(aplanar)
    return _conteos_canal(foto, ahora, canal=canal, cuenta=cuenta,
                          criterio=criterio, aplanado=aplanado,
                          lee_publicaciones=bool(settings.supabase_read_publicaciones),
                          flag=flag)


# ─────────────────────────────────────────────────────────────────────────────
# EL ARMADO EN SU HILO
# ─────────────────────────────────────────────────────────────────────────────

_candado = threading.Lock()          # un armado a la vez; se toma SIN bloquear
_candado_hilo = threading.Lock()     # protege el arranque del hilo
_hilo: threading.Thread | None = None


def refrescar(*, motivo: str, forzar: bool = False) -> bool:
    """Relee las fuentes que tocan (`_fuentes_a_leer`; todas con `forzar` o
    sin foto) y publica la foto. `False` si ya había un armado corriendo (no
    espera), si no tocaba leer nada o si el armado mismo tronó. Bloquea lo que
    tarden las fuentes: se llama desde un hilo, NUNCA desde una corrutina."""
    global _foto
    if not _candado.acquire(blocking=False):
        return False
    try:
        t0 = time.monotonic()
        anterior = _foto
        # Se decide AQUÍ, con el candado tomado, y no en `calentar_en_fondo`:
        # entre aquella pregunta y este armado pudo publicarse otra foto.
        leer = (FUENTES if forzar or anterior is None
                else _fuentes_a_leer(anterior, _ahora()))
        if not leer:
            return False
        kub = _leer("kubera", _leer_kubera) if "kubera" in leer else None
        od = _leer("odoo", _leer_odoo) if "odoo" in leer else None
        dr = _leer("odoo_drop", _leer_drop) if "odoo_drop" in leer else None
        can = _leer("canales", _leer_canales) if "canales" in leer else None
        nueva = armar_foto(kub, od, dr, anterior, canales=can)
        _foto = nueva           # una sola asignación: los lectores ven la vieja o la nueva
        log.info("Flujo del SKU: foto armada (%s; leídas: %s) en %.1f s · %s", motivo,
                 ", ".join(leer), time.monotonic() - t0,
                 ", ".join(f"{n}={'ok' if f.ok else 'vieja' if f.vieja else 'sin dato'}"
                           f"{' SOSPECHOSA' if f.sospechosa else ''}"
                           for n, f in nueva.fuentes.items()))
        return True
    except Exception:  # noqa: BLE001
        log.exception("Flujo del SKU: el armado de la foto falló (%s)", motivo)
        return False
    finally:
        _candado.release()


def calentar_en_fondo(*, forzar: bool = False, motivo: str = "peticion") -> bool:
    """Lanza `refrescar` en un hilo daemon si hace falta. No espera nada.

    `forzar` lo usa el scheduler: con job cada 30 min y TTL de 30 min,
    preguntar «¿ya venció?» se saltaría una vuelta entera."""
    global _hilo
    if not settings.inventario_flujo_enabled:
        return False
    if not forzar and not _debe_refrescar(_foto, _ahora()):
        return False
    with _candado_hilo:
        if (_hilo is not None and _hilo.is_alive()) or _candado.locked():
            return False
        _hilo = threading.Thread(target=refrescar,
                                 kwargs={"motivo": motivo, "forzar": forzar},
                                 name="inventario-flujo", daemon=True)
        _hilo.start()
    return True


def _debe_refrescar(foto: Foto | None, ahora: datetime) -> bool:
    return bool(_fuentes_a_leer(foto, ahora))


def _espera_reintento(fallas: int) -> float:
    """5 min tras la primera falla, 10 tras la segunda, 20… con tope en el TTL:
    una caída que dura horas no rearma cada 5 min para siempre."""
    return float(min(TTL_S, _REINTENTO_FALLA_S * 2 ** max(0, fallas - 1)))


def _fuentes_a_leer(foto: Foto | None, ahora: datetime) -> tuple[str, ...]:
    """Las fuentes que ya toca releer. Una sana, a los `TTL_S` de su último
    intento; una caída (o sospechosa, o que sirvió caché vencida), según
    `_espera_reintento`. Antes cualquier fuente caída rearmaba las TRES cada
    5 min: un DROP caído hasta reiniciar el proceso eran las cuatro lecturas de
    catálogo completo contra Odoo 12 veces por hora en vez de 2."""
    if foto is None:
        return FUENTES
    salida: list[str] = []
    for n in FUENTES:
        f = foto.fuentes.get(n)
        if f is None or f.intento is None:
            salida.append(n)
            continue
        espera = TTL_S if f.ok else _espera_reintento(f.fallas)
        if (ahora - f.intento).total_seconds() >= espera:
            salida.append(n)
    return tuple(salida)


def _motivo_calentando() -> str:
    if not settings.inventario_flujo_enabled:
        return ("INVENTARIO_FLUJO_ENABLED=false: la foto del catálogo no se arma "
                "en este ambiente")
    return "calentando: la foto del catálogo se está armando (12–35 s de Odoo)"


# ── las tres fuentes ────────────────────────────────────────────────────────

def _leer_kubera() -> list[dict[str, Any]]:
    """Una consulta, por los helpers de `supabase_db` (pool + reintento por
    conexión muerta). `statement_timeout` LOCAL a la transacción, como el resto
    de la casa: nunca `set_session`, que en el pooler 6543 se le pega a la
    conexión compartida del siguiente cliente (regla 13)."""
    if not sdb.disponible():
        raise RuntimeError("kubera: SUPABASE_DB_URL no está configurada")
    tope_ms = str(int(settings.inventario_flujo_timeout_s) * 1000)

    def _hacer() -> list[dict[str, Any]]:
        with sdb.get_cursor() as cur:
            cur.execute("select set_config('statement_timeout', %s, true)", (tope_ms,))
            cur.execute(_SQL_KUBERA)
            return [dict(r) for r in cur.fetchall()]

    filas = sdb.reintentar_transitorio(_hacer)
    if not filas:
        raise RuntimeError("kubera: core.products llegó vacío; se trata como falla")
    return filas


def _leer_odoo() -> dict[str, Any]:
    catalogo = odoo.catalogo_productos()
    return {"catalogo": catalogo,
            "quants": odoo.quants_internos_por_producto(),
            "libres": odoo.ids_con_stock_libre(),
            "fotos": odoo.ids_con_foto(),
            # Solo los ids con código repetido y empatados en `active`: una
            # llamada chica para elegir el producto que la tabla elige.
            "existencias": odoo.existencias_por_id(ids_para_desempate(catalogo))}


def _leer_drop() -> tuple[set[str], float]:
    return odoo.estado_almacen("DROP")


def _sql_canales() -> tuple[str, dict[str, Any], list[str]]:
    """La consulta de la cuarta fuente: (sql, params, canales sin «activas»).

    Los predicados NO se re-escriben aquí: se INTERPOLAN los de cada rejilla
    (`channel_read._PUB_ML`/`_PUB_AMZ`, `tiktok_panel`/`walmart_panel`
    `filtro_sql_publicado`, `publicaciones_panel.filtro_sql_activas`). Un
    «publicado» escrito dos veces se desincroniza el día que TikTok cambie de
    vocabulario, y entonces el conteo del stepper y la paginación dejan de
    cuadrar sin que nada falle. Los imports son perezosos, como en el resto de
    la casa: subirlos al encabezado haría ciclo.
    """
    from services import channel_read, publicaciones_panel
    from services import tiktok_panel, walmart_panel

    params: dict[str, Any] = {}
    pub: list[str] = []
    for canal in ("mercado_libre", "amazon"):
        expr, p = channel_read.filtro_sql_publicado(canal)
        pub.append(f"when '{canal}' then ({expr})")
        params.update(p)
    for canal, modulo in (("tiktok", tiktok_panel), ("walmart", walmart_panel)):
        expr, p = modulo.filtro_sql_publicado("l", f"pub_{canal}")
        pub.append(f"when '{canal}' then ({expr})")
        params.update(p)
    # Temu no filtra por «publicados» en su rejilla: todas sus filas SON
    # publicaciones (temu_panel.listar). El conteo copia esa decisión.
    pub.append("when 'temu' then true")

    act: list[str] = []
    sin_activas: list[str] = []
    for canal in ("mercado_libre", "amazon", "tiktok", "temu", "walmart"):
        frag = publicaciones_panel.filtro_sql_activas(canal, alias="l",
                                                      clave=f"act_{canal}")
        if frag is None:
            sin_activas.append(canal)
            continue
        act.append(f"when '{canal}' then ({frag[0]})")
        params.update(frag[1])

    sql = f"""
        select l.sku::text                    as sku,
               l.canal                        as canal,
               coalesce(a.legacy_code, '')    as cuenta,
               (case l.canal {' '.join(pub)} else false end)  as publicado,
               (case l.canal {' '.join(act) or "when '' then false"} else false end) as activa,
               (l.canal = 'mercado_libre'
                and coalesce(l.stock_full, 0) > 0)            as full,
               (p.sku is not null)            as en_catalogo
          from channel.listings l
          left join core.accounts a on a.id = l.account_id
          left join core.products p on p.sku = l.sku
         where l.canal <> 'general'
    """
    return sql, params, sin_activas


def _leer_canales() -> dict[str, Any]:
    """Una consulta (9,283 filas en 0.65 s, medido el 15-sep). Mismo molde que
    `_leer_kubera`: `statement_timeout` LOCAL a la transacción y nunca
    `set_session`, que en el pooler 6543 se le pega a la conexión compartida del
    siguiente cliente (regla 13)."""
    if not sdb.disponible():
        raise RuntimeError("canales: SUPABASE_DB_URL no está configurada")
    sql, params, sin_activas = _sql_canales()
    tope_ms = str(int(settings.inventario_flujo_timeout_s) * 1000)

    def _hacer() -> list[dict[str, Any]]:
        with sdb.get_cursor() as cur:
            cur.execute("select set_config('statement_timeout', %s, true)", (tope_ms,))
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    filas = sdb.reintentar_transitorio(_hacer)
    if not filas:
        raise RuntimeError("canales: channel.listings llegó vacío; se trata como falla")
    return {"filas": filas, "sin_activas": sin_activas}


def _limite_fuente_s() -> float:
    # Las lecturas de Odoo traen timeout por llamada, pero el pool de kubera
    # BLOQUEA sin tope cuando sus 6 conexiones están ocupadas. Sin este techo,
    # una sola llamada colgada dejaría `_candado` tomado para siempre y la foto
    # congelada hasta el siguiente deploy. La fuente `odoo` son hasta seis
    # llamadas al tope (autenticación + cinco lecturas), más holgura.
    return 7.0 * float(settings.inventario_flujo_timeout_s)


# El último hilo de lectura de cada fuente. `_con_limite` deja de esperar a una
# fuente colgada pero no puede matar su hilo; si el siguiente armado lanzara
# otro para la MISMA fuente, los zombis crecerían sin tope mientras siga
# colgada (cada uno con su socket o esperando conexión del pool de kubera, y
# todos corriendo la consulta de golpe cuando el pool se libere). Con este
# registro queda como máximo UN zombi por fuente. Solo se toca desde
# `refrescar`, con `_candado` tomado: no necesita candado propio.
_hilos_fuente: dict[str, threading.Thread] = {}


def _leer(nombre: str, fn: Callable[[], Any]) -> Lectura:
    t0 = time.monotonic()
    colgado = _hilos_fuente.get(nombre)
    if colgado is not None and colgado.is_alive():
        motivo = "la lectura anterior sigue colgada; no se lanza otra"
        log.warning("Flujo del SKU: la fuente %s no se lee: %s", nombre, motivo)
        return Lectura(error=f"TimeoutError: {motivo}", ms=0, generado=_ahora())
    try:
        datos = _con_limite(fn, _limite_fuente_s(), nombre=nombre)
    except Exception as exc:  # noqa: BLE001
        log.warning("Flujo del SKU: la fuente %s falló: %s", nombre, exc)
        return Lectura(error=_texto_error(exc), ms=_ms(t0), generado=_ahora())
    return Lectura(datos=datos, ms=_ms(t0), generado=_ahora())


def _con_limite(fn: Callable[[], Any], segundos: float, *,
                nombre: str | None = None) -> Any:
    """Corre `fn` en un hilo aparte y deja de esperarlo a los `segundos`. El
    hilo colgado se abandona (es daemon): mejor un hilo zombi que la foto
    entera detenida. Con `nombre`, el hilo queda en `_hilos_fuente` para que
    `_leer` no lance otro mientras siga vivo."""
    caja: dict[str, Any] = {}

    def _correr() -> None:
        try:
            caja["datos"] = fn()
        except Exception as exc:  # noqa: BLE001
            caja["error"] = exc

    h = threading.Thread(target=_correr, name="inventario-flujo-fuente", daemon=True)
    if nombre is not None:
        _hilos_fuente[nombre] = h
    h.start()
    h.join(segundos)
    if h.is_alive():
        raise TimeoutError(f"sin respuesta en {segundos:.0f} s")
    if "error" in caja:
        raise caja["error"]
    return caja.get("datos")


# ── utilerías ───────────────────────────────────────────────────────────────

_URL_CON_CREDENCIALES = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE)


def _texto_error(exc: BaseException) -> str:
    """El error que viaja en la respuesta: tipo y mensaje corto, sin URLs (un
    DSN de Postgres en un mensaje de psycopg2 no tiene por qué llegar al rol
    lectura). El detalle completo va al log."""
    texto = _URL_CON_CREDENCIALES.sub("<url>", str(exc)).strip()
    return f"{type(exc).__name__}: {texto}"[:240]


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(v: datetime | None) -> str | None:
    return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if v else None


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)
