"""
ml_guia_tallas.py — La guía de tallas de Mercado Libre, como ML la exige.

POR QUÉ EXISTE
    En ropa y calzado ML exige guía de tallas y el publicador vendorizado
    (`vendor/ml_ready/`) fallaba SIEMPRE: 998 intentos (135 SKUs) y 0 éxitos
    desde abril-2026. Medido el 7-oct-2026 con `POST /items/validate` (no crea
    nada), cambiando una cosa a la vez sobre la réplica de una publicación viva:

        1. `SIZE_GRID_ID` va como value_NAME = id de la guía. Como value_id ML lo
           rechaza siempre — y `publisher_core.py:307` lo manda como value_id.
        2. `SIZE_GRID_ROW_ID` es obligatorio (value_name "<guía>:<renglón>"); ML
           no lo deduce. El vendor no lo manda nunca (0 de 998).
        3. `SIZE` debe ser IDÉNTICO al texto del renglón ("24 MX", no "24").
        4. `GENDER` debe ser el de la guía, y la guía, de ESA cuenta.

    Y los rellenos del mapper que hoy "protege" el propio fallo de la guía: sin
    género manda "Mujer" (el primer valor de la lista), la marca sale como la
    primera de la lista de la categoría (Asics en tenis) y el color se elige por
    subcadena o por "el primero" («Negro / Rosa» salía «Rosa», «Café» salía
    «Coral claro», y una meta heredada del padre le ganaba al eje de la
    variante: 50 de las 50 altas del primer día). Con la guía arreglada esos
    rellenos se publicarían: aquí NO pasan.

QUÉ HACE
    `resolver(prod, cuenta, token)` decide, POR CUENTA y antes de llamar al
    vendor, una de cuatro cosas:
        no_aplica    el dominio no exige guía  → todo sigue como siempre
        ok           hay guía y renglón        → `aplicar()` reescribe el payload
        bloqueado    falta algo                → NO se publica y se dice qué
        desconocido  no se pudo leer la categoría → como siempre (ML frena solo)
    `aplicar(payload, guia, cuenta)` pisa BRAND, GENDER, SIZE, COLOR,
    SIZE_GRID_ID y SIZE_GRID_ROW_ID en el payload que armó el vendor, y deja
    MAIN_COLOR igual al color del producto o lo quita. No toca nada más. Lo
    llaman la vista previa (sobre el payload del dry-run) y la guardia de
    `ml_api.create_item` del adaptador (sobre CADA `POST /items` del alta).
    `etiqueta_error(r, guia, cuenta)` arma el mensaje de un rechazo de ML SOLO
    con las causas `type == "error"`.

DE DÓNDE SALE CADA DATO (nada se adivina; ver `docs/ML_GUIA_TALLAS.md`)
    * TALLA: el atributo «Talla» de WooCommerce con UNA opción. En una variante
      tiene que ser un EJE que ella fija (`prod["ejes_propios"]`): la «Talla» de
      la ficha del padre no es la de la variante. Nunca del SKU, ni del eje
      compuesto «variante», ni de la meta `ml_attr_size`.
    * COLOR: SOLO la estructura de WooCommerce. En una variante, el eje «Color»
      que ella MISMA fija; en un producto simple, el atributo «Color» con una
      opción. No deciden el formulario del Estudio, una meta `ml_attr_*` (en una
      variante es la del padre) ni la lista del padre; si dicen otro color no se
      bloquea: se dice en la vista previa.
    * GÉNERO: decide la PRIMERA fuente que da UN género válido — atributo «Género»
      de Woo (una opción) > formulario del Estudio > meta `ml_attr_GENDER` (la
      escribe la IA de Crear). Basta lo que propone la IA (decisión de Brandon,
      9-oct-2026). El título NUNCA bloquea: si nombra otro género, se dice en la
      vista previa.
    * POR QUÉ EL FORMULARIO DEL ESTUDIO NO MANDA: vuelve SEMBRADO con los
      atributos de WooCommerce (tocado o no) y «Mejorar con IA» lo reemplaza
      entero: desde el backend no se distingue lo que tecleó una persona de lo que
      puso la IA. Lo que una persona fijó de verdad está en WooCommerce.
    * GUÍA: SOLO la registrada en `vendor/ml_ready/size_chart_mapping.py` (la
      config sancionada). No busca guías por su cuenta ni usa las STANDARD de ML.

LÍMITES (a propósito)
    * No crea guías, no adivina género, talla ni color, no desempata renglones.
    * Fuera de los dominios que exigen guía este módulo no cambia nada.
    * Es un FIX, sin interruptor: no hay variable que lo apague ni que lo ajuste.
      La reversa es revertir el commit.

Todo lo que toca la red aquí es SÍNCRONO (`requests`, como el vendor): se llama
con `asyncio.to_thread` (regla 11).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from typing import Any

import requests

from vendor.ml_ready import ml_api, publisher_core, size_chart_mapping

log = logging.getLogger("omnicanal.ml_guia_tallas")

TTL_GUIA_S = 600        # los renglones de una guía solo se AGREGAN: 10 min de caché
TIMEOUT_S = 15          # el mismo de `ml_api.get_category_info`

# Un renglón cuyo largo de pie y su talla MX difieren en más de esto no se usa. En las
# 23 guías propias (SPECIFIC) leídas el 7-oct, de 159 renglones con los dos datos la
# diferencia es 0.0 en 158 y 10 cm en uno (`4859025:6`: «28 MX» y 38 cm): en las guías
# propias la escala MX ES el largo del pie en centímetros. (En las STANDARD de ML no: ahí
# «MX» rotula una escala tipo US; no se usan.)
TOLERANCIA_PIE_CM = 2.0

# Los 59 dominios de `GET /catalog/charts/MLM/configurations/active_domains`,
# leídos el 7-oct-2026 (la ruta es pública y ese día devolvía exactamente estos).
# Si ML agrega uno y aquí falta, el alta sale como siempre, ML la rechaza y
# `etiqueta_error` dice que hay que agregarlo: falla hacia lo de hoy.
DOMINIOS_CON_GUIA = frozenset({
    "AMERICAN_FOOTBALL_JERSEYS", "BABIES_FOOTWEAR", "BASEBALL_AND_SOFTBALL_JERSEYS",
    "BASKETBALL_JERSEYS", "BLOUSES", "BOOTS_AND_BOOTIES", "BRAS", "CYCLING_JERSEYS",
    "DANCE_SNEAKERS_AND_SHOES", "DRESSES", "ESPADRILLES", "FASHION_KIMONOS",
    "FISHING_PANTS", "FISHING_SHIRTS", "FISHING_VESTS", "FLATS", "FOOTBALL_JACKETS",
    "FOOTBALL_SHIRTS", "FOOTBALL_SHOES", "FOOTBALL_SWEATSHIRTS_AND_HOODIES",
    "HEELS_AND_WEDGES", "HIKING_BOOTS", "JACKETS_AND_COATS", "LEGGINGS",
    "LOAFERS_AND_OXFORDS", "MOTORCYCLE_BOOTS", "NEOPRENE_BOOTS", "NIGHTGOWNS",
    "PAJAMAS", "PANTIES", "PANTS", "PANTS_TEST", "PONCHOS", "ROBES", "RUGBY_JERSEYS",
    "SAFETY_FOOTWEAR", "SANDALS_AND_CLOGS", "SCHOOL_SMOCKS", "SHIRTS", "SHORTS",
    "SKIRTS", "SLIPPERS", "SNEAKERS", "SNEAKERS_TEST", "SPORT_PANTS", "SPORT_SHORTS",
    "SPORT_SKIRTS", "SPORT_T_SHIRTS", "SWEATERS_AND_CARDIGANS",
    "SWEATSHIRTS_AND_HOODIES", "TACTICAL_BOOTS", "TACTICAL_PANTS", "THERMAL_SHIRTS",
    "TUTUS", "T_SHIRTS", "UNDERPANTS", "VESTS", "WADERS", "WORK_PANTS",
})

# Los siete valores de GENDER de ML (lista cerrada). Lo que no sea uno de estos
# NO se traduce ("Unisex", "Dama"): no decide (se pasa a la fuente siguiente) y solo
# bloquea si ninguna fuente da un género válido.
GENEROS = ("Mujer", "Hombre", "Niñas", "Niños", "Bebés", "Sin género", "Sin género infantil")

# Lo único que `aplicar` pisa SIEMPRE en el payload del vendor.
ATRIBUTOS_QUE_PISA = ("BRAND", "GENDER", "SIZE", "COLOR", "SIZE_GRID_ID", "SIZE_GRID_ROW_ID")
# Y lo que AJUSTA solo si el vendor lo manda: `MAIN_COLOR` sale de la meta que la IA
# escribió para el PADRE (lo llevaban 985 de 1,001 intentos) y no es obligatorio en
# ninguna de las 53 categorías de moda leídas: se iguala al color del producto si empata
# EXACTO con su lista, y si no, se quita. Nunca queda un color que contradiga a COLOR.
ATRIBUTOS_QUE_AJUSTA = ("MAIN_COLOR",)

# FAMILIAS QUE NO SE PUBLICAN DESDE EL PANEL (a mano, sin variable): el SKU —o el de su
# padre— es la familia o empieza por «<familia>-». Se quita de aquí cuando se corrija.
#   CALZ-0134 / 0129 / 0127   en sus publicaciones vivas la persona eligió, de forma
#                             sistemática, OTRO renglón que el de la columna EU de la guía
#   CALZ-0089 / 0090 / 0200   traen tabla de tallas propia (`_kubera_size_chart`) que puede
#                             contradecir la guía
FAMILIAS_EN_REVISION = frozenset({
    "CALZ-0134", "CALZ-0129", "CALZ-0127", "CALZ-0089", "CALZ-0090", "CALZ-0200"})

# Bloqueos que son del PRODUCTO (iguales en las dos cuentas): en la vista previa
# se dicen una sola vez.
CODIGOS_DEL_PRODUCTO = frozenset({
    "sin_genero", "genero_invalido", "genero_fuera_de_categoria", "color_no_fijado",
    "sin_talla", "talla_no_fijada", "guia_en_revision"})

# Veredictos que dependen del CONTENIDO de la guía: si salieron de la copia en caché se
# relee UNA vez antes de bloquear (quien corrigió la guía en ML no espera 10 minutos).
CODIGOS_QUE_RELEEN = frozenset({"talla_sin_renglon", "talla_ambigua", "guia_no_sirve"})

# ── Mensajes (lo que ve la persona; las pruebas comparan contra estos) ───────
# La salida de TODO bloqueo de género: el atributo «Género» de WooCommerce con una sola
# opción es lo único que una persona fijó de verdad en el producto, y manda.
SALIDA_GENERO = (
    "Cómo se arregla: en WooCommerce, deja en el producto el atributo «Género» con UNA sola "
    "opción (ese manda sobre el formulario del Estudio y sobre la meta de la IA)")
MSG_SIN_GENERO = (
    "Guía de tallas: falta el GÉNERO{hay}. Mercado Libre exige guía de tallas en {dominio} y "
    "la guía se elige por género; no se adivina. Tiene que ser uno de: {validos}. "
    + SALIDA_GENERO + ". (También basta el «Género» del formulario del Estudio —ahí lo deja "
    "«Mejorar con IA»— o el que propuso la IA al crear el producto.)")
MSG_GENERO_INVALIDO = (
    "Guía de tallas: el género «{valor}» ({origen}) no es un género de Mercado Libre. Usa "
    "exactamente uno de: {validos}. " + SALIDA_GENERO + ".")
MSG_GENERO_FUERA = (
    "Guía de tallas: el género «{genero}» no existe en la categoría {categoria} ({dominio}): "
    "ahí solo vale {validos}. Revisa la categoría de Mercado Libre del producto; si la "
    "categoría está bien, lo que está mal es el género. " + SALIDA_GENERO + ".")
MSG_COLOR_NO_FIJADO_VARIANTE = (
    "Guía de tallas: esta variante no tiene COLOR propio{hay}. El color solo se toma del eje "
    "de variación «Color» que la variante fija en WooCommerce (sus ejes: {ejes}); nunca de la "
    "ficha del padre, ni de una meta heredada, ni del formulario del Estudio, porque Mercado "
    "Libre anunciaría el color de otra variante. Cómo se arregla: en WooCommerce, en el "
    "producto padre, marca «Color» como atributo «usado para variaciones» y fíjalo en esta "
    "variante.")
MSG_COLOR_NO_FIJADO_SIMPLE = (
    "Guía de tallas: el producto no tiene un COLOR fijado{hay}. El color solo se toma del "
    "atributo «Color» de WooCommerce con UNA sola opción; no de la meta que propuso la IA, ni "
    "del formulario del Estudio, ni del título, porque el publicador mandaría el primero de "
    "su lista. Cómo se arregla: en WooCommerce, deja un solo valor en el atributo «Color» del "
    "producto (un color compuesto va en UN valor: «Negro / Rosa»).")
MSG_SIN_TALLA_VARIANTE = (
    "Guía de tallas: esta variante no tiene TALLA propia. Se lee del eje de variación «Talla» "
    "que la variante fija en WooCommerce y no lo tiene (sus ejes: {ejes}); no se toma del "
    "SKU, ni del eje «variante», ni de la ficha del padre. Cómo se arregla: en WooCommerce, "
    "en el producto padre, marca «Talla» como atributo «usado para variaciones» y fíjala en "
    "esta variante.")
MSG_SIN_TALLA = (
    "Guía de tallas: el producto no tiene TALLA. Se lee del atributo «Talla» de WooCommerce y "
    "no existe; no se toma del SKU ni del título. Cómo se arregla: agrega el atributo «Talla» "
    "al producto con un solo valor (una publicación = una talla).")
MSG_TALLA_DEL_PADRE = (
    "Guía de tallas: la Talla que hay ({opciones}) es la del producto padre: esta variante no "
    "la fija (sus ejes: {ejes}). La variante debe fijar un EJE de talla: en WooCommerce, en "
    "el producto padre, marca «Talla» como atributo «usado para variaciones» y fíjala en esta "
    "variante. No se publica con la talla de otra variante.")
MSG_TALLA_NO_FIJADA = (
    "Guía de tallas: el atributo «Talla» trae {n} opciones ({opciones}) y no dice cuál es la "
    "de este SKU. Deja una sola en WooCommerce (una publicación = una talla).")
PISTA_CON_GUIAS = "En {cuenta} hay guía registrada para: {generos}."
PISTA_SIN_GUIAS = ("Ojo: en {cuenta} todavía no hay ninguna guía registrada para {dominio}; "
                   "además habrá que crearla.")
MSG_SIN_GUIA = (
    "Guía de tallas: no hay guía registrada para {cuenta} · {dominio} · {genero}, y Mercado "
    "Libre la exige en esta categoría. {pista} Hay que crearla en la cuenta {cuenta} de "
    "Mercado Libre y registrar su id en backend/vendor/ml_ready/size_chart_mapping.py.")
MSG_EN_REVISION = (
    "Guía de tallas: la familia {familia} está EN REVISIÓN y no se publica desde el panel: "
    "sus hermanas vivas se publicaron a mano con otro renglón que el de la guía, o el producto "
    "trae tabla de tallas propia que contradice la guía. Publícalo a mano en Mercado Libre, o "
    "quita {familia} de FAMILIAS_EN_REVISION (backend/services/ml_guia_tallas.py) cuando se "
    "corrija.")
MSG_GUIA_ILEGIBLE = (
    "Guía de tallas: no se pudo leer la guía {guia_id} de {cuenta} en Mercado Libre "
    "({detalle}). Esta categoría no se publica sin guía, así que no se envió nada. Vuelve a "
    "intentar; si se repite con 404, revisa el id en "
    "backend/vendor/ml_ready/size_chart_mapping.py.")
MSG_GUIA_NO_SIRVE = (
    "Guía de tallas: la guía {guia_id} registrada para {cuenta} · {dominio} · {genero} no se "
    "puede usar: {razon}. {arreglo}")
ARREGLO_MAPPING = "Corrige backend/vendor/ml_ready/size_chart_mapping.py."
ARREGLO_RENGLON = ("Esa tabla es la que ve el comprador; mientras no se corrija, la talla "
                   "«{talla}» no se publica en {cuenta}.")
RAZON_RENGLON_IMPOSIBLE = "el renglón {renglon_id} {dice}; corrígelo en Mercado Libre"
MSG_TALLA_SIN_RENGLON = (
    "Guía de tallas: la talla «{talla}» no está en la guía {guia_id} «{nombre}» de {cuenta} "
    "({dominio} · {genero}). Renglones de la guía: {tallas}{eu}. Agrega ese renglón a la guía "
    "en Mercado Libre o corrige la talla del producto.")
MSG_TALLA_AMBIGUA = (
    "Guía de tallas: la talla «{talla}» cae en {n} renglones de la guía {guia_id} de "
    "{cuenta}: {renglones}.{escalas} No se elige uno por ti. En el Estudio, en el atributo "
    "Talla de esta variante, escribe el texto exacto del renglón correcto (p. ej. "
    "«{ejemplo}»), o corrige la guía en Mercado Libre.")
NOTA_DOS_ESCALAS = (" El número está a la vez en la columna EU y en la MX de esta guía: no "
                    "se sabe en qué escala viene.")
MSG_ERROR_INTERNO = (
    "Guía de tallas: error inesperado al resolver la guía de {cuenta} ({detalle}). Esta "
    "categoría no se publica sin guía, así que no se envió nada. Vuelve a intentar; si se "
    "repite, busca «guia_tallas» en los logs.")
MSG_MODA_ACTIVA = (
    "Guía de tallas: se creó en Mercado Libre y NO quedó pausado: {lista}. No se crea nada "
    "más de moda hasta revisarlo. Páusalo en Mercado Libre: el candado se suelta solo en "
    "cuanto el panel lo lee en un estado que no sea «active».")
MSG_MODA_ITEM = "{item_id} en {cuenta} ({estado})"
MODA_ESTADO_LEIDO = "su estado en Mercado Libre es «{estado}»"
MODA_ESTADO_SIN_LEER = "no se pudo leer su estado en Mercado Libre"
MSG_NO_SE_PUEDE = "No se puede publicar en Mercado Libre."
MSG_AVISO_OK = (
    "{cuenta} · guía de tallas {guia_id} «{nombre}»: talla {talla} → renglón {renglon_id} "
    "«{size}»{como}. Género {genero} ({origen_genero}), marca {marca}.")
MSG_ESTUDIO_NO_USADO = (
    " En el Estudio la Talla dice «{escrita}»: no se usó, manda la de WooCommerce.")
MSG_AVISO_BLOQUEADO = "{cuenta} · NO se publicará. {motivo}"
MSG_AVISO_SIN_TOKEN = "{cuenta} · sin token de Mercado Libre: no se revisó su guía de tallas."
MSG_SIN_TOKEN_EN_MOTIVO = ("{cuenta}: sin token de Mercado Libre en este momento; no se revisó "
                           "su guía de tallas (vuelve a intentar).")
MSG_PAYLOAD_DE = "El payload de abajo es el de {cuenta}."
MSG_PARCIAL = (
    "Si confirmas, se creará SOLO en {listas}. Ojo: después «Actualizar» no agrega la cuenta "
    "que faltó (el panel solo actualiza donde ya existe la publicación).")
MSG_PARCIAL_HECHA = (
    "Se creó SOLO en {listas}; {faltaron} no se creó (el motivo está en su renglón). Ojo: "
    "«Actualizar» no agrega después la cuenta que faltó (el panel solo actualiza donde ya "
    "existe la publicación).")
MSG_COLOR = "Color: saldrá como «{color}» ({origen}{libre}).{nota}{vendor}{principal}"
# Lo que el formulario del Estudio o la meta dicen del color y NO se usa (no bloquea).
MSG_COLOR_NO_USADO = "{dicen}; {es} «{color}»: se publica «{color}»"
COLOR_ES_VARIANTE = "la variante es"
COLOR_ES_SIMPLE = "el atributo Color de WooCommerce es"
MSG_TITULO = (
    "Título: Mercado Libre arma el título de la publicación con este texto + color + talla, "
    "y el tuyo ya trae {que}: saldrá repetido y cada talla quedará como publicación aparte. "
    "Para que las tallas se junten en una sola ficha usa el MISMO título, sin talla ni color, "
    "en todas las variantes.")
MSG_TIPO = (
    "Categoría: el título nombra {tipos} y la categoría de Mercado Libre del producto es de "
    "{dominio}. La guía de tallas que se aplica es la de esa categoría, y la misma talla EU "
    "cambia hasta 2 cm de una guía a otra. Si el producto no es de ese tipo, corrige la "
    "categoría antes de publicar.")
ERR_OTRA_CUENTA = (
    "Guía de tallas: la guía se resolvió para {esperada} y el payload es de {recibida}. No se envía.")
ERR_OTRA_CATEGORIA = (
    "Guía de tallas: el payload salió en la categoría {recibida} y la guía se resolvió para "
    "{esperada}. No se envía.")
ERR_SIN_FAMILY = (
    "Guía de tallas: el publicador no pudo leer la categoría {cat} en Mercado Libre (el "
    "payload salió sin family_name). No se envía una publicación a medias. Puede ser que "
    "Mercado Libre no contestó o que el token de la cuenta venció; vuelve a intentar.")
RAZON_GUIA_ENVIADA = (
    "GUIA_DE_TALLAS_RECHAZADA ({code}): Mercado Libre contestó «{mensaje}». Se envió la guía "
    "{guia_id}, renglón {renglon_id}, SIZE «{size}», GENDER «{genero}», COLOR «{color}» en "
    "{cuenta}")
RAZON_GUIA_SIN_RESOLVER = (
    "GUIA_DE_TALLAS ({code}): Mercado Libre exige guía de tallas en esta categoría y el alta "
    "salió sin ella: {porque}")
# Las tres razones que el vendor ya sabía decir, con SU texto (publisher_core.py:642-647):
# `publicar_ready._traducir_error_ml` busca el de ME1 literal para traducirlo.
_RAZONES_VENDOR = {
    "shipping.lost_me1_by_user": "ME1_INACTIVO (activar Mercado Envíos 1 en dashboard ML)",
    "invalid.title.gender": ("TITLE_GENDER_MISMATCH (revisar título y atributo GENDER del "
                             "producto en WC)"),
    "item.pictures.invalid_size": "IMAGES_TOO_SMALL (subir imágenes ≥500x250 px al producto en WC)",
}
_DETALLE_HTTP = {
    # En moda el alta ya NO llega al `POST /items` que le renovaba el token al vendor
    # (publisher_core.py:406-413): este paso no lo renueva, y no se promete que "se renueva
    # solo" (regla 8 de CLAUDE.md: el backend solo se auto-sana al llegar un pedido).
    "HTTP 401": ("HTTP 401: el token de la cuenta venció. En moda este paso no lo renueva: se "
                 "renueva cuando entra un pedido de esa cuenta o al publicar en ella algo que "
                 "no es de moda; después se puede reintentar"),
    "HTTP 403": ("HTTP 403: la app con la que publica el panel no tiene permiso para leer "
                 "guías de tallas"),
    "HTTP 404": ("HTTP 404: Mercado Libre dice que esa guía no existe, o que esta cuenta no "
                 "puede verla"),
}


class GuiaTallasError(RuntimeError):
    """El payload no cuadra con la guía resuelta: NO se envía (ver `aplicar`).

    `codigo`: 'otra_cuenta' y 'otra_categoria' son deterministas (no se reintentan);
    'sin_family' se reintenta UNA sola vez (el vendor no pudo leer la categoría)."""

    def __init__(self, mensaje: str, codigo: str = "") -> None:
        super().__init__(mensaje)
        self.codigo = codigo


# ── Normalización (solo para COMPARAR; a ML viaja el texto del renglón) ──────

def _plano(texto: Any) -> str:
    """Minúsculas, sin acentos, espacios colapsados."""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.casefold().split())


_GENERO_POR_PLANO = {_plano(g): g for g in GENEROS}


def genero_canonico(valor: Any) -> str | None:
    """'Hombre', "['Hombre']", 'sin genero' → el nombre exacto de ML. Lo demás, None.
    (`"['Hombre']"` es la huella de la IA de Crear: el repr de una lista; se limpia igual
    que lo limpia el vendor, publisher_core.py:297-299.)"""
    if isinstance(valor, (list, tuple)):
        valor = valor[0] if len(valor) == 1 else ""
    return _GENERO_POR_PLANO.get(_plano(str(valor or "").strip().strip("[]'\" ")))


def marca_de(prod: dict[str, Any]) -> str:
    """La marca DECLARADA (metas antes que atributos, como el mapper) o la de la casa."""
    for d in (prod.get("ml_attrs") or {}, prod.get("wc_attrs") or {}):
        for k, v in d.items():
            if _plano(k) in ("brand", "marca"):
                limpio = str(v or "").strip().strip("[]'\" ")
                if limpio:
                    return limpio
    return publisher_core.DEFAULT_BRAND


# ── Lo que es de ESTE SKU: talla y color (nunca los de la ficha del padre) ───

def _eje(nombre: Any) -> str:
    """'Talla', 'pa_talla', 'PA_Color' → 'talla' / 'color'. Solo para comparar nombres."""
    p = _plano(nombre)
    return p[3:] if p.startswith("pa_") else p


def es_variacion(prod: dict[str, Any]) -> bool:
    """¿Es una variación de WooCommerce? Lo dicen las dos llaves que `construir_prod`
    agrega: `padre_wc_id` y `ejes_propios` (sus metas `attribute_*` con valor)."""
    return bool(prod.get("padre_wc_id")) or bool(prod.get("ejes_propios"))


def ejes_propios(prod: dict[str, Any]) -> list[str]:
    """Los ejes de variación que ESTA variante fija, con su nombre de eje ('talla', …)."""
    return [_eje(e) for e in (prod.get("ejes_propios") or [])]


def atributo_propio(prod: dict[str, Any], nombre: str) -> tuple[str | None, str, list[str]]:
    """
    (valor, estado, opciones) del atributo `nombre` ('talla' | 'color') que es DE ESTE SKU.

        ok         una sola opción y es suya            → (valor, 'ok', [])
        del_padre  es una variación y ese eje NO lo fija ella: viene de la ficha del padre
        varias     producto simple con varias opciones  (no dice cuál es la de este SKU)
        ausente    no hay atributo con ese nombre

    En una VARIACIÓN `prod["attributes"]` trae, para el eje que ella no fija, las opciones
    del padre (`wp_db._atributos_de_variacion`): si el padre declara «Talla = 40» de un solo
    valor, todas las hijas lo reciben como si fuera suyo (el 42 se publicaba como 40). Por
    eso aquí manda `prod["ejes_propios"]`.
    """
    variacion, propios = es_variacion(prod), set(ejes_propios(prod))
    visto: tuple[str | None, str, list[str]] = (None, "ausente", [])
    for a in prod.get("attributes") or []:
        if not isinstance(a, dict) or _eje(a.get("name")) != nombre:
            continue
        opciones = [str(o).strip() for o in (a.get("options") or []) if str(o).strip()]
        if not opciones:
            continue
        if variacion and nombre not in propios:
            estado: tuple[str | None, str, list[str]] = (None, "del_padre", opciones)
        elif len(opciones) > 1:
            estado = (None, "varias", opciones)
        else:
            return opciones[0], "ok", []
        if visto[1] == "ausente":
            visto = estado
    return visto


def talla_de(prod: dict[str, Any]) -> tuple[str | None, str, list[str]]:
    """(talla, estado, opciones) — ver `atributo_propio`. Lee `prod["attributes"]` (lo que
    WooCommerce tiene) y NO `wc_attrs`, donde el formulario del Estudio puede traer un valor
    heredado del padre o de otra hermana."""
    return atributo_propio(prod, "talla")


def _del_formulario(prod: dict[str, Any], nombres: tuple[str, ...]) -> list[str]:
    """
    Lo que el formulario del Estudio trae para alguno de esos atributos y NO es una opción
    de WooCommerce para ese mismo atributo: lo que trae `campos.atributos` (que
    `construir_prod` deja en `prod["atributos_panel"]`), sin la semilla.

    LO QUE ESTO NO PRUEBA: que una persona lo eligió. El Estudio SIEMBRA su formulario con
    los atributos de WooCommerce (`wp_db.metadata_producto`) y los manda de vuelta al
    publicar, tocados o no —en una variación, la semilla del eje que ella no fija es LA
    PRIMERA opción de la lista del padre (`(a["options"] or [""])[0]`)—; y «Mejorar con
    IA» reemplaza TODAS las filas por las que propone la IA (`ProductStudio.tsx`), que
    viajan tal cual al publicar. Un valor igual a una opción de Woo es la semilla; uno
    distinto vino del formulario, de una persona o de la IA: el backend no los distingue.
    Por eso el formulario NO decide el color y, en el género, va DESPUÉS del atributo de
    WooCommerce.
    """
    de_woo = {_plano(o) for a in (prod.get("attributes") or [])
              if isinstance(a, dict) and _eje(a.get("name")) in nombres
              for o in (a.get("options") or [])}
    return [str(v).strip() for k, v in (prod.get("atributos_panel") or {}).items()
            if _eje(k) in nombres and str(v or "").strip()
            and " | " not in str(v) and _plano(v) not in de_woo]


ORIGEN_COLOR_EJE = "eje Color de la variante"
ORIGEN_COLOR_WOO = "atributo Color de WooCommerce"


def _meta_de_color(prod: dict[str, Any]) -> str:
    """La meta `ml_attr_COLOR` que NO cuenta (solo para decirlo en el mensaje)."""
    for k, v in (prod.get("ml_attrs") or {}).items():
        if _plano(k) == "color" and str(v or "").strip():
            return str(v).strip()
    return ""


def color_de(prod: dict[str, Any]) -> dict[str, Any]:
    """
    El color de ESTE SKU. Solo lo decide la ESTRUCTURA de WooCommerce:
        * en una variación, el eje «Color» que ella MISMA fija;
        * en un producto simple, el atributo «Color» con UNA sola opción.
    NO deciden: el formulario del Estudio (ver `_del_formulario`: con «Mejorar con IA»,
    dos hermanas «Negro / Rosa» y «Negro / Morado» salían las dos «Negro»), una meta
    `ml_attr_*` (en una variante es la del PADRE) ni la lista de colores del padre.

    Si el formulario o la meta dicen OTRO color no se bloquea: va en `nota`, para la línea
    de la vista previa. Devuelve {"valor": str | None, "origen", "nota", "estado", "opciones"}.
    """
    valor, estado, opciones = atributo_propio(prod, "color")
    if estado != "ok" or valor is None:
        return {"valor": None, "origen": "", "nota": "", "estado": estado, "opciones": opciones}
    variacion = es_variacion(prod)
    dicen: list[str] = []
    formulario = [v for v in _del_formulario(prod, ("color",)) if _plano(v) != _plano(valor)]
    if formulario:
        dicen.append("el formulario del Estudio dice "
                     + ", ".join(f"«{v[:40]}»" for v in formulario))
    meta = _meta_de_color(prod).strip("[]'\" ")
    if meta and _plano(meta) != _plano(valor):
        dicen.append(f"la meta ml_attr_COLOR dice «{meta[:40]}»")
    nota = MSG_COLOR_NO_USADO.format(
        dicen=" y ".join(dicen), color=valor,
        es=COLOR_ES_VARIANTE if variacion else COLOR_ES_SIMPLE) if dicen else ""
    return {"valor": valor, "origen": ORIGEN_COLOR_EJE if variacion else ORIGEN_COLOR_WOO,
            "nota": nota, "estado": "ok", "opciones": []}


_PALABRAS = {"extra chica": "XS", "chica": "S", "mediana": "M", "grande": "L",
             "extra grande": "XL", "doble extra grande": "2XL"}
_RE_LETRA = re.compile(r"^(?:(\d{1,2})X|(X{1,5}))?(S|CH|L|G)$")


def letra_canonica(texto: Any) -> str | None:
    """S/CH → S · G/L → L · XG/XL → XL · XXL/2XG → 2XL · M → M. Lo que no es letra, None.
    (L ≡ G lo dice la columna FILTRABLE_SIZE de las guías de brasier: ML rotula «G» la L.)
    El prefijo numérico solo vale desde 2: en tallas extra «0X» y «1X» no son L ni XL, así
    que `0XL` y `1XL` no tienen equivalencia (solo empatan con un renglón escrito igual)."""
    p = _plano(texto)
    if p in _PALABRAS:
        return _PALABRAS[p]
    t = re.sub(r"[\s.\-]", "", str(texto or "").upper())
    if t == "M":
        return "M"
    m = _RE_LETRA.match(t)
    if not m:
        return None
    if m.group(1):
        n = int(m.group(1))
        if n < 2:
            return None
    else:
        n = len(m.group(2) or "")
    base = "S" if m.group(3) in ("S", "CH") else "L"
    return base if n == 0 else ("X" + base if n == 1 else f"{n}X{base}")


def _texto_norm(texto: Any) -> str:
    t = str(texto or "").upper().replace("½", ".5").replace(",", ".")
    return re.sub(r"\s+", "", t)


_RE_NUM = re.compile(r"^\d{1,3}(?:\.\d+)?$")


def numero(texto: Any) -> float | None:
    """'40' → 40.0 · '23½' → 23.5 · '24,5' → 24.5. '24 MX', 'M', '42-43' → None."""
    t = _texto_norm(texto)
    return float(t) if _RE_NUM.match(t) else None


def talla_del_estudio(prod: dict[str, Any], talla_woo: str) -> str | None:
    """Lo que trae el atributo Talla del formulario del Estudio, si es distinto de la talla
    de WooCommerce. SOLO sirve para elegir entre los renglones en los que esa talla ya cayó
    (ver `_resolver_con_guia`): nunca la reemplaza. Como todo el formulario, puede haberlo
    escrito una persona o «Mejorar con IA»: la línea de la vista previa lo avisa."""
    for k, v in (prod.get("wc_attrs") or {}).items():
        if _plano(k) in ("talla", "pa_talla", "size"):
            t = str(v or "").strip()
            if t and _texto_norm(t) != _texto_norm(talla_woo):
                return t
    return None


# ── Género: decide la primera fuente que da UN género válido; el título solo informa ──

RANGO_WOO, RANGO_FORMULARIO, RANGO_META = 1, 2, 3
ORIGEN_WOO = "atributo Género de WooCommerce"
# UN solo «:» y justo antes de «revísalo»: `decidir_genero` corta ahí para el mensaje de
# género inválido.
ORIGEN_FORMULARIO = "formulario del Estudio; puede venir de «Mejorar con IA»: revísalo"
ORIGEN_META = "lo propuso la IA al crear el producto: revísalo"
_NOMBRE_DE_FUENTE = {RANGO_WOO: "el atributo Género de WooCommerce",
                     RANGO_FORMULARIO: "el formulario del Estudio",
                     RANGO_META: "la meta de Crear"}
_ORIGEN_DE_RANGO = {RANGO_WOO: ORIGEN_WOO, RANGO_FORMULARIO: ORIGEN_FORMULARIO,
                    RANGO_META: ORIGEN_META}
_LLAVES_GENERO = ("gender", "genero")

# Las palabras con las que un título NOMBRA un género (palabra entera, sin acentos).
_GENERO_EN_TITULO = {
    "Mujer": ("mujer", "mujeres", "dama", "damas"),
    "Hombre": ("hombre", "hombres", "caballero", "caballeros"),
    "Niñas": ("nina", "ninas"),
    "Niños": ("nino", "ninos"),
    "Bebés": ("bebe", "bebes"),
}
_SIN_GENERO = ("Sin género", "Sin género infantil")


def fuentes_de_genero(prod: dict[str, Any]) -> list[tuple[int, list[str]]]:
    """[(rango, valores crudos)] de las fuentes que traen ALGO, de mayor a menor rango:

        1  el atributo «Género» de WooCommerce — CADA opción cuenta (un atributo con dos
           opciones son dos géneros, no «el primero»). Es lo único que una persona fijó
           de verdad en el producto
        2  el formulario del Estudio: lo que trae `prod["atributos_panel"]` y NO es una
           opción del atributo de WooCommerce (ver `_del_formulario`: puede haberlo
           escrito una persona o haberlo puesto «Mejorar con IA»)
        3  la meta `ml_attr_gender` / `ml_attr_GENDER` (la escribe la IA de Crear)

    El relleno del mapper del vendor no cuenta: aquí no se lee el payload."""
    woo = [str(o).strip() for a in (prod.get("attributes") or [])
           if isinstance(a, dict) and _eje(a.get("name")) in _LLAVES_GENERO
           for o in (a.get("options") or []) if str(o or "").strip()]
    formulario = _del_formulario(prod, _LLAVES_GENERO)
    meta = [str(v).strip() for k, v in (prod.get("ml_attrs") or {}).items()
            if _plano(k) in _LLAVES_GENERO and str(v or "").strip()]
    return [(rango, valores) for rango, valores in (
        (RANGO_WOO, woo), (RANGO_FORMULARIO, formulario), (RANGO_META, meta)) if valores]


def generos_del_titulo(titulo: Any) -> tuple[list[tuple[str, str]], bool]:
    """([(género, palabra del título)], ¿dice «unisex»?). Palabra entera; se COMPARA sin
    acentos y se devuelve como está escrita, en minúsculas («niña», no «nina»): es lo que
    la persona lee en la línea de la vista previa."""
    palabras = re.findall(r"[a-z0-9]+", _plano(titulo))
    escritas = re.findall(r"[^\W_]+", unicodedata.normalize("NFC", str(titulo or "")).lower())
    como_esta = {_plano(p): p for p in escritas}
    nombrados: list[tuple[str, str]] = []
    for genero, formas in _GENERO_EN_TITULO.items():
        dicha = next((p for p in palabras if p in formas), None)
        if dicha:
            nombrados.append((genero, como_esta.get(dicha, dicha)))
    return nombrados, "unisex" in palabras


def decidir_genero(prod: dict[str, Any]) -> dict[str, Any]:
    """
    El género del producto: decide la PRIMERA fuente que da UN solo género válido, en este
    orden: WooCommerce > formulario del Estudio > meta de la IA (ver `fuentes_de_genero`).
    Basta lo que propone la IA. Devuelve {"genero": str | None, "codigo", …}:

      * una fuente con géneros distintos, o con un valor que no es uno de los siete, NO
        decide ni bloquea por sí sola: se pasa a la siguiente y se dice en la vista previa;
      * si ninguna decide → `genero_invalido` (hay un valor que no es de ML: cuál y de
        dónde salió) o `sin_genero` (`hay`: lo que sí hay y no decide);
      * el título NUNCA bloquea: si nombra un género que no es el decidido, se dice en la
        vista previa. «unisex» es compatible con «Sin género» / «Sin género infantil».
    """
    fuentes = fuentes_de_genero(prod)

    def dicho(valores: list[str]) -> str:
        return ", ".join(f"«{v[:40]}»" for v in dict.fromkeys(
            v.strip("[]'\" ") for v in valores))

    genero: str | None = None
    rango = 0
    invalido: tuple[int, str] | None = None
    notas: list[str] = []
    for rango, crudos in fuentes:
        canonicos = {genero_canonico(c) for c in crudos}
        genero = next(iter(canonicos)) if len(canonicos) == 1 else None
        if genero is not None:
            break
        notas.append(f"{_NOMBRE_DE_FUENTE[rango]} trae {dicho(crudos)} y no decide")
        if invalido is None and None in canonicos:
            invalido = (rango, next(c for c in crudos if genero_canonico(c) is None))
    if genero is None:
        if invalido is not None:
            return {"genero": None, "codigo": "genero_invalido", "valor": invalido[1][:60],
                    "origen": _ORIGEN_DE_RANGO[invalido[0]].split(":")[0]}
        return {"genero": None, "codigo": "sin_genero", "hay": "; ".join(notas)}
    for otro_rango, otros in fuentes:
        distintos = [o for o in otros if genero_canonico(o) != genero]
        if otro_rango > rango and distintos:
            notas.append(f"{_NOMBRE_DE_FUENTE[otro_rango]} decía {dicho(distintos)}")
    nombrados, unisex = generos_del_titulo(prod.get("title"))
    contradice = [palabra for g, palabra in nombrados if g != genero]
    if contradice and not (unisex and genero in _SIN_GENERO):
        notas.append("el título dice " + ", ".join(f"«{p}»" for p in contradice))
    return {"genero": genero, "codigo": "", "rango": rango,
            "origen": "; ".join([_ORIGEN_DE_RANGO[rango], *notas])}


# ── Familias en revisión (a mano: `FAMILIAS_EN_REVISION`) ────────────────────

def familia_en_revision(prod: dict[str, Any]) -> str | None:
    """La familia de `FAMILIAS_EN_REVISION` a la que pertenece el producto, o None: su SKU,
    o el de su padre (`prod["padre_sku"]`), ES la familia o empieza por «<familia>-»."""
    for sku in (prod.get("sku"), prod.get("padre_sku")):
        s = str(sku or "").strip().upper()
        for familia in sorted(FAMILIAS_EN_REVISION):
            if s == familia or s.startswith(familia + "-"):
                return familia
    return None


# ── Lectura de una guía (forma de `GET /catalog/charts/{id}`) ────────────────

def _valor(renglon: dict[str, Any], attr_id: str) -> dict[str, Any] | None:
    for a in renglon.get("attributes") or []:
        if isinstance(a, dict) and a.get("id") == attr_id:
            valores = a.get("values") or []
            return valores[0] if valores and isinstance(valores[0], dict) else None
    return None


def _num_valor(v: dict[str, Any] | None) -> float | None:
    if not v:
        return None
    n = (v.get("struct") or {}).get("number")
    if isinstance(n, (int, float)) and not isinstance(n, bool):
        return float(n)
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)", str(v.get("name") or ""))
    return float(m.group(1).replace(",", ".")) if m else None


def _con_unidad(v: dict[str, Any] | None, unidad: str) -> float | None:
    """El número de un valor de la guía SOLO si viene en esa unidad («28 MX», «38 cm»)."""
    if not v:
        return None
    st = v.get("struct") or {}
    n = st.get("number")
    if (isinstance(n, (int, float)) and not isinstance(n, bool)
            and str(st.get("unit") or "").strip().lower() == unidad.lower()):
        return float(n)
    m = re.match(rf"^\s*(\d+(?:[.,]\d+)?)\s*{re.escape(unidad)}\s*$",
                 str(v.get("name") or ""), re.IGNORECASE)
    return float(m.group(1).replace(",", ".")) if m else None


def mx_de_renglon(renglon: dict[str, Any]) -> float | None:
    """La talla MX del renglón (columna MX_SIZE, o su SIZE si está en MX)."""
    for attr_id in ("MX_SIZE", "SIZE"):
        n = _con_unidad(_valor(renglon, attr_id), "MX")
        if n is not None:
            return n
    return None


def renglon_imposible(renglon: dict[str, Any]) -> str:
    """'' si el renglón es coherente (o no trae los dos datos); si no, lo que dice:
    «dice 28 MX y 38 cm de largo de pie». Solo aplica cuando el renglón trae talla MX y
    largo de pie en centímetros."""
    mx, pie = mx_de_renglon(renglon), _con_unidad(_valor(renglon, "FOOT_LENGTH"), "cm")
    if mx is None or pie is None or abs(mx - pie) <= TOLERANCIA_PIE_CM:
        return ""
    return f"dice {'%g' % mx} MX y {'%g' % pie} cm de largo de pie"


def _fila(r: dict[str, Any], como: str) -> tuple[str, str, str, str] | None:
    rid = str(r.get("id") or "").strip()
    size = str((_valor(r, "SIZE") or {}).get("name") or "").strip()
    if not rid or not size:
        return None
    pie = str((_valor(r, "FOOT_LENGTH") or {}).get("name") or "").strip()
    return rid, size, como, pie


def renglones_para(guia: dict[str, Any], talla: str) -> list[tuple[str, str, str, str]]:
    """[(id del renglón, SIZE del renglón, cómo empató, largo de pie)] de TODOS los renglones
    que empatan con la talla. Un renglón empata por la PRIMERA de estas que se cumpla:

        texto  su SIZE es la talla (sin mirar mayúsculas, espacios ni coma/punto)
        letra  es la misma talla de letra (L ≡ G, XXL ≡ 2XL, CH ≡ S)
        EU     la talla es un número y es el EU_SIZE del renglón

    El id y el SIZE se COPIAN del renglón: los ids no son posicionales (las guías de ML
    traen huecos) y hay tres escrituras vivas («22.5 MX», «22,5 MX», «2,0 MX»). Un número
    suelto NUNCA se busca en la columna MX (0 de 153 publicaciones vivas de calzado
    coinciden con MX; 115 con EU). Aquí NO se desempata: si regresan dos, quien llama decide.
    """
    t_txt, t_letra, t_num = _texto_norm(talla), letra_canonica(talla), numero(talla)
    fuera: dict[str, tuple[str, str, str, str]] = {}
    for r in guia.get("rows") or []:
        if not isinstance(r, dict):
            continue
        fila = _fila(r, "")
        if fila is None:
            continue
        como = ""
        if _texto_norm(fila[1]) == t_txt:
            como = "texto"
        elif t_letra and letra_canonica(fila[1]) == t_letra:
            como = "letra"
        elif t_num is not None and _num_valor(_valor(r, "EU_SIZE")) == t_num:
            como = "EU"
        if como:
            fuera[fila[0]] = (fila[0], fila[1], como, fila[3])
    return list(fuera.values())


def renglones_por_mx(guia: dict[str, Any], talla: str) -> list[tuple[str, str, str, str]]:
    """Los renglones cuya talla MX es ese número. NO es una forma de empatar: solo sirve
    para detectar que un número está a la vez en la columna EU y en la MX de la misma guía
    (pasa en guías infantiles: EU 21–35 contra MX 13–22), y entonces no se elige."""
    t_num = numero(talla)
    if t_num is None:
        return []
    fuera = []
    for r in guia.get("rows") or []:
        if isinstance(r, dict) and mx_de_renglon(r) == t_num:
            fila = _fila(r, "MX")
            if fila is not None:
                fuera.append(fila)
    return fuera


def _renglon_por_id(guia: dict[str, Any], renglon_id: str) -> dict[str, Any]:
    for r in guia.get("rows") or []:
        if isinstance(r, dict) and str(r.get("id") or "").strip() == renglon_id:
            return r
    return {}


def tallas_de_guia(guia: dict[str, Any]) -> list[str]:
    return [s for s in (str((_valor(r, "SIZE") or {}).get("name") or "").strip()
                        for r in guia.get("rows") or [] if isinstance(r, dict)) if s]


def eus_de_guia(guia: dict[str, Any]) -> list[str]:
    fuera: list[str] = []
    for r in guia.get("rows") or []:
        n = _num_valor(_valor(r, "EU_SIZE")) if isinstance(r, dict) else None
        if n is not None and ("%g" % n) not in fuera:
            fuera.append("%g" % n)
    return fuera


def genero_de_guia(guia: dict[str, Any]) -> list[dict[str, Any]]:
    for a in guia.get("attributes") or []:
        if isinstance(a, dict) and a.get("id") == "GENDER":
            return [v for v in (a.get("values") or []) if isinstance(v, dict)]
    return []


def _por_que_no_sirve(guia: dict[str, Any], dominio: str, genero: str, marca: str) -> str:
    """'' si la guía se puede usar; si no, la razón en una frase."""
    tipo = str(guia.get("type") or "")
    if tipo != "SPECIFIC":
        return (f"es de tipo {tipo or 'desconocido'} y solo se usan guías propias de la "
                "cuenta (SPECIFIC)")
    if str(guia.get("chart_status") or "").upper() == "INACTIVE":
        return "está dada de baja en Mercado Libre (chart_status INACTIVE)"
    suyo = str(guia.get("domain_id") or "").replace("MLM-", "")
    if suyo != dominio:
        return f"es del dominio {suyo or '?'}, no de {dominio}"
    nombres = [str(v.get("name") or "") for v in genero_de_guia(guia)]
    if nombres != [genero]:
        return f"es para el género «{', '.join(nombres) or '?'}», no para «{genero}»"
    extra: list[str] = []
    for a in guia.get("attributes") or []:
        if not isinstance(a, dict) or a.get("id") == "GENDER":
            continue
        if a.get("id") == "BRAND":
            # La doc de ML (9-jul-2026) pide BRAND en la cabecera de las guías nuevas;
            # ninguna de las 45 leídas el 7-oct lo trae. Si viene, tiene que ser la del producto.
            marcas = [str(v.get("name") or "") for v in (a.get("values") or [])
                      if isinstance(v, dict)]
            if _plano(marca) not in {_plano(m) for m in marcas}:
                return (f"es de la marca «{', '.join(marcas) or '?'}» y el producto es "
                        f"«{marca}»")
            continue
        extra.append(str(a.get("id")))
    if extra:
        return (f"además del género exige {', '.join(extra)}, que el panel todavía no "
                "sabe comprobar")
    if not tallas_de_guia(guia):
        return "no tiene renglones"
    return ""


# ── Avisos que no bloquean: el tipo de calzado que nombra el título ──────────

# (cómo se dice, palabras del título sin acentos, dominios en los que ESE tipo es el suyo).
# Lista corta y explícita: si el título no nombra ninguno, no se dice nada.
_TIPOS_DE_CALZADO = (
    ("zapato de seguridad",
     r"(?:zapatos?|calzado|botas?|botin(?:es)?|tenis) de seguridad", {"SAFETY_FOOTWEAR"}),
    # `[sz]`: 27 SKUs de CALZ-0181 dicen «ZANDALIAS DE CORRER» y están en SNEAKERS.
    ("sandalia o huarache", r"[sz]andalias?|huaraches?", {"SANDALS_AND_CLOGS"}),
    ("bota o botín", r"botas?|botin(?:es)?",
     {"BOOTS_AND_BOOTIES", "HIKING_BOOTS", "MOTORCYCLE_BOOTS", "NEOPRENE_BOOTS",
      "TACTICAL_BOOTS", "WADERS"}),
    ("tenis", r"tenis", {"SNEAKERS", "SNEAKERS_TEST", "DANCE_SNEAKERS_AND_SHOES",
                         "FOOTBALL_SHOES"}),
    ("mocasín", r"mocasin(?:es)?", {"LOAFERS_AND_OXFORDS"}),
    ("pantufla", r"pantuflas?", {"SLIPPERS"}),
    ("tacón o zapatilla", r"tacon(?:es)?|zapatillas?", {"HEELS_AND_WEDGES"}),
)
_DOMINIOS_DE_CALZADO = frozenset().union(*(d for _, _, d in _TIPOS_DE_CALZADO))


def aviso_de_tipo(titulo: Any, dominio: str) -> str:
    """'' o el aviso (NO bloquea) de que el título nombra un tipo de calzado que no es el
    del dominio de la categoría: la guía sigue a la categoría, y 78 SKUs que el título
    llama «sandalia» («zandalia») o «zapato de seguridad» están en SNEAKERS. Es un aviso
    por palabras: también salta donde la palabra no contradice a la categoría
    («zapatillas de ballet» en DANCE_SNEAKERS_AND_SHOES, «zapatillas de playa» en sandalias)."""
    if dominio not in _DOMINIOS_DE_CALZADO:
        return ""
    resto = _plano(titulo)
    nombrados: list[tuple[str, set[str]]] = []
    for etiqueta, patron, dominios in _TIPOS_DE_CALZADO:
        expresion = re.compile(rf"(?<![0-9a-z])(?:{patron})(?![0-9a-z])")
        if expresion.search(resto):
            nombrados.append((etiqueta, dominios))
            resto = expresion.sub(" ", resto)       # «bota de seguridad» no cuenta como bota
    if not nombrados or any(dominio in dominios for _, dominios in nombrados):
        return ""
    return MSG_TIPO.format(tipos=", ".join(f"«{e}»" for e, _ in nombrados), dominio=dominio)


# ── Lo que se lee de ML (síncrono: va en un hilo) ────────────────────────────

_candado = threading.Lock()
_dominios: dict[str, str] = {}                       # category_id → dominio ('' = sin dominio)
_attrs_cat: dict[str, dict[str, dict[str, Any]]] = {}  # category_id → {GENDER, COLOR, MAIN_COLOR}
_guias: dict[str, tuple[float, dict[str, Any]]] = {}  # id de guía → (cuándo, guía)
_IDS_DE_CATEGORIA = ("GENDER", "COLOR", "MAIN_COLOR")


def categoria_efectiva(prod: dict[str, Any]) -> str:
    """La categoría con la que el vendor va a armar el payload (publisher_core.py:185-196)."""
    guardada = str(prod.get("ml_category_id") or "")
    try:
        from vendor.ml_ready.wc_category_mapping import resolve_ml_category_from_wc
        nueva, motivo = resolve_ml_category_from_wc(prod.get("wc_categories", []), guardada)
    except Exception:  # noqa: BLE001 — el vendor hace lo mismo y sigue con la guardada
        nueva, motivo = None, "error"
    return str(nueva) if (nueva and motivo == "override") else guardada


def _get_publico(ruta: str) -> Any:
    """GET SIN token a una ruta pública de ML. El JSON, o None si no contestó 200."""
    try:
        r = requests.get(f"{ml_api.ML_API_BASE}{ruta}", timeout=TIMEOUT_S)
        return r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def _dominio_de(category_id: str, token: str) -> tuple[str | None, str]:
    """(dominio sin 'MLM-', '') o (None, por qué no se pudo leer). Solo se recuerdan los
    aciertos: el dominio de una categoría no cambia, un fallo sí."""
    with _candado:
        if category_id in _dominios:
            return _dominios[category_id], ""
    detalle = "Mercado Libre no devolvió la categoría"
    info: Any = None
    if token:          # sin token (ver `dominio_exige_guia`) se va directo a la ruta pública
        try:
            # El MISMO lector que usa el vendor un instante después (ml_api.py:65-74).
            info = ml_api.get_category_info(category_id, token)
        except Exception as exc:  # noqa: BLE001
            detalle = type(exc).__name__
    if not (isinstance(info, dict) and str(info.get("id") or "") == str(category_id)):
        # `/categories/{id}` es pública (200 sin token, medido el 7-oct-2026): un token
        # vencido no debe dejar sin saber si la categoría exige guía.
        info = _get_publico(f"/categories/{category_id}")
    if not (isinstance(info, dict) and str(info.get("id") or "") == str(category_id)):
        return None, detalle
    dominio = str((info.get("settings") or {}).get("catalog_domain") or "").replace("MLM-", "")
    with _candado:
        _dominios[category_id] = dominio
    return dominio, ""


def _valores_de_categoria(category_id: str, token: str) -> dict[str, dict[str, Any]] | None:
    """Lo que la categoría dice de GENDER, COLOR y MAIN_COLOR —{id: {"valores": [{id, name}],
    "required": bool}}, solo los que trae— o None si no se pudo leer. Un fallo NO bloquea ni
    se recuerda: simplemente no se valida contra la categoría."""
    with _candado:
        if category_id in _attrs_cat:
            return _attrs_cat[category_id]
    attrs: Any = None
    try:
        attrs = ml_api.get_category_attributes(category_id, token)   # [] si falló
    except Exception:  # noqa: BLE001
        attrs = None
    if not (isinstance(attrs, list) and attrs):
        attrs = _get_publico(f"/categories/{category_id}/attributes")
    if not (isinstance(attrs, list) and attrs):
        return None
    fuera: dict[str, dict[str, Any]] = {}
    for a in attrs:
        if isinstance(a, dict) and a.get("id") in _IDS_DE_CATEGORIA:
            fuera[str(a["id"])] = {
                "required": bool((a.get("tags") or {}).get("required")),
                "valores": [{"id": str(v.get("id") or ""), "name": str(v.get("name") or "").strip()}
                            for v in (a.get("values") or [])
                            if isinstance(v, dict) and str(v.get("name") or "").strip()]}
    with _candado:
        _attrs_cat[category_id] = fuera
    return fuera


def _valor_exacto(valores: list[dict[str, Any]] | None, texto: Any) -> dict[str, Any] | None:
    """El valor de la lista cuyo nombre es EXACTAMENTE el texto (sin mirar acentos,
    mayúsculas ni espacios dobles). Nunca subcadena, nunca «el primero»: «Negro / Rosa» no
    es «Rosa» y «Café» no es «Coral claro»."""
    p = _plano(texto)
    if not p:
        return None
    for v in valores or []:
        if isinstance(v, dict) and _plano(v.get("name")) == p and v.get("id"):
            return v
    return None


def _color_para_ml(color: str, valores: dict[str, dict[str, Any]] | None
                   ) -> tuple[str, str, dict[str, Any]]:
    """(value_name, value_id | '', qué hacer con MAIN_COLOR) para el color del producto.

    COLOR: si empata EXACTO con un valor de la lista de la categoría, viaja con su id; si
    no, viaja el texto LITERAL por value_name (ML acepta color libre: 41 de las 159
    publicaciones vivas de moda lo llevan, 21 de ellas sin value_id).

    MAIN_COLOR (lo manda el vendor desde una meta que no es de este SKU): se iguala si el
    color empata exacto con SU lista; si no empata y no es obligatorio, se quita; si la
    categoría lo exige y no hay dato, se deja como lo manda el vendor y se dice."""
    exacto = _valor_exacto(((valores or {}).get("COLOR") or {}).get("valores"), color)
    nombre, valor_id = (exacto["name"], exacto["id"]) if exacto else (str(color).strip(), "")
    principal = (valores or {}).get("MAIN_COLOR")
    if not principal:
        accion: dict[str, Any] = {"accion": "quitar"}
    else:
        igual = _valor_exacto(principal.get("valores"), color)
        if igual:
            accion = {"accion": "igualar", "value_id": igual["id"], "value_name": igual["name"]}
        elif not principal.get("valores"):
            accion = {"accion": "igualar", "value_id": "", "value_name": str(color).strip()}
        elif principal.get("required"):
            accion = {"accion": "dejar"}
        else:
            accion = {"accion": "quitar"}
    return nombre, valor_id, accion


def _leer_guia(guia_id: str, token: str,
               fresca: bool = False) -> tuple[dict[str, Any] | None, str, bool]:
    """(guía, '', ¿salió de la caché?) o (None, por qué, False). `GET /catalog/charts/{id}`
    con el token de la cuenta. Un reintento solo ante red/429/5xx; un fallo nunca se guarda.
    `fresca=True` se salta la caché (y la renueva si ML contesta)."""
    ahora = time.monotonic()
    if not fresca:
        with _candado:
            guardada = _guias.get(guia_id)
            if guardada and ahora - guardada[0] < TTL_GUIA_S:
                return guardada[1], "", True
    detalle = ""
    for intento in (1, 2):
        try:
            r = requests.get(f"{ml_api.ML_API_BASE}/catalog/charts/{guia_id}",
                             headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001
            detalle = type(exc).__name__
        else:
            if r.status_code == 200:
                try:
                    guia = r.json()
                except Exception:  # noqa: BLE001
                    guia = None
                if (isinstance(guia, dict) and str(guia.get("id") or "") == str(guia_id)
                        and isinstance(guia.get("rows"), list)):
                    with _candado:
                        _guias[guia_id] = (ahora, guia)
                    return guia, "", False
                return None, "la respuesta no tiene la forma de una guía", False
            detalle = f"HTTP {r.status_code}"
            if r.status_code not in (429, 500, 502, 503, 504):
                break
        if intento == 1:
            time.sleep(1.0)
    return None, _DETALLE_HTTP.get(detalle, detalle or "sin respuesta"), False


def generos_registrados(cuenta: str, dominio: str) -> list[str]:
    """Géneros con guía registrada en size_chart_mapping.py para esa cuenta y dominio."""
    fuera: list[str] = []
    for clave in (size_chart_mapping.CHARTS_BY_ACCOUNT.get(cuenta) or {}):
        dom, _, genero = str(clave).partition(":")
        if dom == dominio and genero:
            fuera.append(genero)
    return fuera


def _exige_guia(dominio: str) -> bool:
    if dominio in DOMINIOS_CON_GUIA:
        return True
    # Un dominio nuevo de ML que alguien ya registró en el mapping también cuenta.
    return any(generos_registrados(c, dominio) for c in size_chart_mapping.CHARTS_BY_ACCOUNT)


def dominio_exige_guia(prod: dict[str, Any], token: str = "") -> bool | None:
    """¿El dominio de la categoría de este producto exige guía de tallas? None si no se
    pudo saber (sin categoría, o Mercado Libre no devolvió la categoría).

    Es la pregunta BARATA: solo mira `ml_category_id` y `wc_categories` —no resuelve
    género, talla, color ni guía— y el dominio de una categoría se recuerda. Sin token lee
    la categoría por la ruta pública. SÍNCRONA (la primera vez lee de ML): va en un hilo.
    Nunca lanza."""
    try:
        categoria = categoria_efectiva(prod)
        if not categoria:
            return None
        dominio, _ = _dominio_de(categoria, token)
        return None if dominio is None else _exige_guia(dominio)
    except Exception:  # noqa: BLE001
        log.exception("guia_tallas: dominio_exige_guia reventó")
        return None


# ── El resolvedor ────────────────────────────────────────────────────────────

def resolver(prod: dict[str, Any], cuenta: str, token: str) -> dict[str, Any]:
    """
    Qué hacer con la guía de tallas de ESTE producto en ESTA cuenta.

    SÍNCRONA a propósito (lee de ML con `requests`): quien la llama desde una
    corrutina usa `asyncio.to_thread`. Nunca lanza. No muta `prod`.

    Devuelve siempre `estado`, `codigo`, `motivo`, `cuenta`, `category_id`, `dominio`; con
    `estado == "bloqueado"` además `codigos`, `motivo_base` y `pista`; con `estado == "ok"`
    `genero`, `genero_id`, `origen_genero`, `genero_rango`, `marca`, `guia_id`,
    `guia_nombre`, `renglon_id`, `size`, `talla`, `como`, `pie`, `a_mano`, `talla_estudio`,
    `color`, `color_id`, `color_origen`, `color_nota`, `main_color` y `aviso_tipo`.
    """
    base: dict[str, Any] = {"estado": "no_aplica", "codigo": "", "motivo": "",
                            "cuenta": cuenta, "category_id": "", "dominio": ""}
    exige = False
    try:
        categoria = categoria_efectiva(prod)
        base["category_id"] = categoria
        if not categoria:
            return base            # el vendor dirá "Faltan datos: categoría ML"
        dominio, detalle = _dominio_de(categoria, token)
        if dominio is None:
            log.warning("guia_tallas: no se pudo leer la categoría %s (%s): %s — el alta "
                        "sigue como siempre", categoria, cuenta, detalle)
            return {**base, "estado": "desconocido", "codigo": "categoria_ilegible",
                    "motivo": detalle}
        base["dominio"] = dominio
        if not _exige_guia(dominio):
            return base
        exige = True               # de aquí en adelante solo se sale con "ok" o "bloqueado"
        r = _resolver_con_guia(prod, cuenta, token, base)
        sku = prod.get("sku")
        if r["estado"] == "ok":
            log.info("guia_tallas sku=%s cuenta=%s dominio=%s estado=ok genero=%s talla=%r "
                     "guia=%s renglon=%s size=%r por=%s%s color=%r", sku, cuenta, dominio,
                     r["genero"], r["talla"], r["guia_id"], r["renglon_id"], r["size"],
                     r["como"], " (elegido en el Estudio)" if r["a_mano"] else "", r["color"])
        else:
            log.info("guia_tallas sku=%s cuenta=%s dominio=%s estado=bloqueado codigo=%s",
                     sku, cuenta, dominio, ",".join(r.get("codigos") or [r["codigo"]]))
        return r
    except Exception as exc:  # noqa: BLE001
        log.exception("guia_tallas: resolver reventó (%s / %s)", cuenta,
                      prod.get("sku") if isinstance(prod, dict) else "?")
        detalle = f"{type(exc).__name__}: {exc}"[:160]
        if exige:
            motivo = MSG_ERROR_INTERNO.format(cuenta=cuenta, detalle=detalle)
            return {**base, "estado": "bloqueado", "codigo": "error_interno",
                    "codigos": ["error_interno"], "motivo": motivo, "motivo_base": motivo,
                    "pista": ""}
        return {**base, "estado": "desconocido", "codigo": "error_interno", "motivo": detalle}


def _lista(valores: list[str], tope: int = 12) -> str:
    return ", ".join(valores[:tope]) + ("…" if len(valores) > tope else "")


def _resolver_con_guia(prod: dict[str, Any], cuenta: str, token: str,
                       base: dict[str, Any]) -> dict[str, Any]:
    dominio, categoria = base["dominio"], base["category_id"]

    def bloqueo(codigo: str, motivo: str, pista: str = "",
                codigos: list[str] | None = None) -> dict[str, Any]:
        return {**base, "estado": "bloqueado", "codigo": codigo,
                "codigos": codigos or [codigo], "motivo_base": motivo, "pista": pista,
                "motivo": f"{motivo} {pista}".strip()}

    # ── 0. Las familias en revisión no se publican desde el panel (ni se lee su guía).
    familia = familia_en_revision(prod)
    if familia:
        return bloqueo("guia_en_revision", MSG_EN_REVISION.format(familia=familia))

    # ── 1. Lo que es del PRODUCTO (igual en las dos cuentas): género, color y talla.
    #       Se revisan los tres y se dicen JUNTOS, para no descubrir el segundo faltante
    #       después de arreglar el primero.
    faltas: list[tuple[str, str]] = []
    registrados = generos_registrados(cuenta, dominio)
    valores_cat = _valores_de_categoria(categoria, token)
    de_categoria = (None if valores_cat is None else
                    [v["name"] for v in (valores_cat.get("GENDER") or {}).get("valores") or []])
    validos = ", ".join(de_categoria or GENEROS)
    variacion = es_variacion(prod)
    ejes = _lista(ejes_propios(prod)) or "ninguno"
    decidido = decidir_genero(prod)
    genero: str | None = decidido["genero"]
    if decidido["codigo"] == "sin_genero":
        faltas.append(("sin_genero", MSG_SIN_GENERO.format(
            hay=f" ({decidido['hay']})" if decidido["hay"] else "",
            dominio=dominio, validos=validos)))
    elif decidido["codigo"] == "genero_invalido":
        faltas.append(("genero_invalido", MSG_GENERO_INVALIDO.format(
            valor=decidido["valor"], origen=decidido["origen"], validos=validos)))
    elif de_categoria and genero not in de_categoria:
        faltas.append(("genero_fuera_de_categoria", MSG_GENERO_FUERA.format(
            genero=genero, categoria=categoria, dominio=dominio,
            validos=", ".join(f"«{g}»" for g in de_categoria))))
        genero = None
    color = color_de(prod)
    if color["valor"] is None:
        # Qué hay que NO cuenta, para que el mensaje no mande a buscar donde no es.
        heredada = _meta_de_color(prod)
        no_cuenta: list[str] = []
        if color["estado"] == "del_padre":
            no_cuenta.append(f"el Color que hay, {_lista(color['opciones'])}, es el del "
                             "producto padre")
        elif color["estado"] == "varias":
            no_cuenta.append(f"el atributo «Color» trae {len(color['opciones'])} opciones: "
                             f"{_lista(color['opciones'])}")
        elif heredada:
            no_cuenta.append(f"la meta ml_attr_COLOR «{heredada[:40]}» no cuenta: "
                             + ("es la del producto padre" if variacion else "la propuso la IA"))
        del_formulario = _del_formulario(prod, ("color",))
        if del_formulario:
            no_cuenta.append("el formulario del Estudio dice "
                             + ", ".join(f"«{v[:40]}»" for v in del_formulario)
                             + ": tampoco cuenta")
        hay = f" ({'; '.join(no_cuenta)})" if no_cuenta else ""
        faltas.append(("color_no_fijado", (
            MSG_COLOR_NO_FIJADO_VARIANTE.format(hay=hay, ejes=ejes) if variacion
            else MSG_COLOR_NO_FIJADO_SIMPLE.format(hay=hay))))
    talla, estado_talla, opciones = talla_de(prod)
    if estado_talla == "del_padre":
        faltas.append(("talla_no_fijada", MSG_TALLA_DEL_PADRE.format(
            opciones=_lista(opciones), ejes=ejes)))
    elif estado_talla == "varias":
        faltas.append(("talla_no_fijada", MSG_TALLA_NO_FIJADA.format(
            n=len(opciones), opciones=_lista(opciones))))
    elif talla is None:
        faltas.append(("sin_talla", MSG_SIN_TALLA_VARIANTE.format(ejes=ejes) if variacion
                       else MSG_SIN_TALLA))
    if faltas:
        pista = ""
        if any(c == "sin_genero" for c, _ in faltas):
            pista = (PISTA_CON_GUIAS.format(cuenta=cuenta, generos=", ".join(registrados))
                     if registrados else PISTA_SIN_GUIAS.format(cuenta=cuenta, dominio=dominio))
        # Juntos y en una frase: el primero completo, los demás sin repetir el rótulo.
        juntos = [faltas[0][1]] + [m.removeprefix("Guía de tallas: ") for _, m in faltas[1:]]
        return bloqueo(faltas[0][0], " Además: ".join(juntos), pista, [c for c, _ in faltas])
    if genero is None or talla is None:      # no puede pasar: sin faltas, los dos existen
        raise RuntimeError("género o talla sin resolver")

    # ── 2. La guía: SOLO la registrada en la config sancionada.
    guia_id = size_chart_mapping.get_chart_id(cuenta, dominio, genero)
    if not guia_id:
        pista = (f"En {cuenta} solo hay guía de {dominio} para: {', '.join(registrados)}."
                 if registrados else f"En {cuenta} no hay ninguna guía de {dominio}.")
        return bloqueo("sin_guia", MSG_SIN_GUIA.format(
            cuenta=cuenta, dominio=dominio, genero=genero, pista=pista))
    guia_id = str(guia_id)

    # ── 3. Leerla de ML. Si no se puede, NO se publica sin guía.
    marca = marca_de(prod)
    guia, detalle, de_cache = _leer_guia(guia_id, token)
    if guia is None:
        log.warning("guia_tallas: no se pudo leer la guía %s (%s): %s", guia_id, cuenta, detalle)
        return bloqueo("guia_ilegible", MSG_GUIA_ILEGIBLE.format(
            guia_id=guia_id, cuenta=cuenta, detalle=detalle))
    escrita = talla_del_estudio(prod, talla)

    def con(guia: dict[str, Any]) -> dict[str, Any]:
        """El veredicto con ESTA copia de la guía."""
        razon = _por_que_no_sirve(guia, dominio, genero, marca)
        if razon:
            return bloqueo("guia_no_sirve", MSG_GUIA_NO_SIRVE.format(
                guia_id=guia_id, cuenta=cuenta, dominio=dominio, genero=genero, razon=razon,
                arreglo=ARREGLO_MAPPING))
        nombres = guia.get("names")
        nombre = str((nombres.get("MLM") if isinstance(nombres, dict) else nombres) or "").strip()

        # ── 4. El renglón: exactamente uno.
        hallados = renglones_para(guia, talla)
        if not hallados:
            eus = eus_de_guia(guia)
            if eus:
                eu = f" (tallas EU: {', '.join(eus)})"
            elif numero(talla) is not None:
                eu = (" (esta guía no tiene columna EU: un número suelto no se puede ubicar; "
                      "hay que completarle la columna EU en Mercado Libre o registrar otra guía)")
            else:
                eu = ""
            return bloqueo("talla_sin_renglon", MSG_TALLA_SIN_RENGLON.format(
                talla=talla, guia_id=guia_id, nombre=nombre, cuenta=cuenta, dominio=dominio,
                genero=genero, tallas=", ".join(tallas_de_guia(guia)), eu=eu))
        dos_escalas = False
        if any(h[2] == "EU" for h in hallados):
            # El mismo número en la columna EU y en la MX de ESTA guía: no se sabe en qué
            # escala viene la talla. Se suman los renglones de la otra lectura para que NO
            # salga por uno solo; la persona elige en el Estudio con el texto del renglón.
            ya = {h[0] for h in hallados}
            otros = [h for h in renglones_por_mx(guia, talla) if h[0] not in ya]
            if otros:
                hallados, dos_escalas = hallados + otros, True
        elegido_a_mano = False
        if len(hallados) > 1 and escrita:
            # La ÚNICA salida humana: lo escrito en el Estudio elige entre los renglones en
            # los que la talla del producto YA cayó. No abre ningún renglón que no sea suyo.
            suyos = [h for h in hallados if _texto_norm(h[1]) == _texto_norm(escrita)]
            if len(suyos) == 1:
                hallados, elegido_a_mano = suyos, True
        if len(hallados) > 1:
            return bloqueo("talla_ambigua", MSG_TALLA_AMBIGUA.format(
                talla=talla, n=len(hallados), guia_id=guia_id, cuenta=cuenta,
                renglones=", ".join(f"«{s}» ({rid})" for rid, s, _, _ in hallados),
                escalas=NOTA_DOS_ESCALAS if dos_escalas else "", ejemplo=hallados[0][1]))
        renglon_id, size, como, pie = hallados[0]
        dice = renglon_imposible(_renglon_por_id(guia, renglon_id))
        if dice:
            # Un renglón con un dato imposible es la tabla que verá el comprador: no se usa.
            return bloqueo("guia_no_sirve", MSG_GUIA_NO_SIRVE.format(
                guia_id=guia_id, cuenta=cuenta, dominio=dominio, genero=genero,
                razon=RAZON_RENGLON_IMPOSIBLE.format(renglon_id=renglon_id, dice=dice),
                arreglo=ARREGLO_RENGLON.format(talla=talla, cuenta=cuenta)))
        valor_genero = genero_de_guia(guia)[0]
        color_nombre, color_id, principal = _color_para_ml(color["valor"], valores_cat)
        return {**base, "estado": "ok", "codigo": "ok",
                "genero": genero, "genero_id": str(valor_genero.get("id") or ""),
                "origen_genero": decidido["origen"], "genero_rango": decidido["rango"],
                "marca": marca,
                "guia_id": guia_id, "guia_nombre": nombre, "renglon_id": renglon_id,
                "size": size, "talla": talla, "como": como, "pie": pie,
                "a_mano": elegido_a_mano,
                # Lo escrito en el Estudio que NO se usó (se dice en la vista previa).
                "talla_estudio": "" if elegido_a_mano else (escrita or ""),
                "color": color_nombre, "color_id": color_id,
                "color_origen": color["origen"], "color_nota": color["nota"],
                "main_color": principal,
                "aviso_tipo": aviso_de_tipo(prod.get("title"), dominio)}

    veredicto = con(guia)
    if de_cache and veredicto["codigo"] in CODIGOS_QUE_RELEEN:
        # Quien acaba de corregir la guía en Mercado Libre no tiene por qué esperar los 10
        # minutos de la caché: antes de bloquear por su CONTENIDO se relee UNA vez.
        nueva, _, _ = _leer_guia(guia_id, token, fresca=True)
        if nueva is not None:
            veredicto = con(nueva)
    return veredicto


# ── Aplicarlo al payload del vendor ──────────────────────────────────────────

def aplicar(payload: dict[str, Any], guia: dict[str, Any], cuenta: str) -> dict[str, Any]:
    """
    Pisa BRAND, GENDER, SIZE, COLOR, SIZE_GRID_ID y SIZE_GRID_ROW_ID en el payload que armó
    `publisher_core.build_payload`, y ajusta MAIN_COLOR si el vendor lo mandó (lo iguala al
    color del producto o lo quita: ver `_color_para_ml`). Nada más cambia. Idempotente:
    aplicarlo dos veces da lo mismo (la guardia de `create_item` lo repite en cada POST de la
    cadena del vendor).

    Lanza `GuiaTallasError` si el payload no es el que se resolvió (otra cuenta, otra
    categoría, o sin `family_name`: señal de que el vendor no pudo leer la categoría):
    antes que mandar una publicación a medias, no se manda.
    """
    if guia.get("estado") != "ok":
        return payload
    if guia.get("cuenta") != cuenta:
        raise GuiaTallasError(ERR_OTRA_CUENTA.format(
            esperada=guia.get("cuenta"), recibida=cuenta), "otra_cuenta")
    if str(payload.get("category_id") or "") != str(guia.get("category_id") or ""):
        raise GuiaTallasError(ERR_OTRA_CATEGORIA.format(
            recibida=payload.get("category_id"), esperada=guia.get("category_id")),
            "otra_categoria")
    if not payload.get("family_name"):
        raise GuiaTallasError(ERR_SIN_FAMILY.format(cat=guia.get("category_id")), "sin_family")
    genero: dict[str, Any] = {"id": "GENDER", "value_name": guia["genero"]}
    if guia.get("genero_id"):
        genero = {"id": "GENDER", "value_id": guia["genero_id"], "value_name": guia["genero"]}
    color: dict[str, Any] = {"id": "COLOR", "value_name": guia["color"]}
    if guia.get("color_id"):
        color = {"id": "COLOR", "value_id": guia["color_id"], "value_name": guia["color"]}
    viejos = [a for a in (payload.get("attributes") or []) if isinstance(a, dict)]
    quita = set(ATRIBUTOS_QUE_PISA)
    ajustados: list[dict[str, Any]] = []
    principal = guia.get("main_color") or {}
    if principal.get("accion") in ("igualar", "quitar") and any(
            a.get("id") == "MAIN_COLOR" for a in viejos):
        quita.add("MAIN_COLOR")
        if principal["accion"] == "igualar":
            nuevo = {"id": "MAIN_COLOR", "value_name": principal["value_name"]}
            if principal.get("value_id"):
                nuevo = {"id": "MAIN_COLOR", "value_id": principal["value_id"],
                         "value_name": principal["value_name"]}
            ajustados.append(nuevo)
    payload["attributes"] = [a for a in viejos if a.get("id") not in quita] + [
        {"id": "BRAND", "value_name": guia["marca"]},
        genero,
        {"id": "SIZE", "value_name": guia["size"]},
        color,
        {"id": "SIZE_GRID_ID", "value_name": guia["guia_id"]},
        {"id": "SIZE_GRID_ROW_ID", "value_name": guia["renglon_id"]},
    ] + ajustados
    return payload


def huella(payload: dict[str, Any]) -> str:
    """El payload como texto, con los atributos ordenados: dos envíos con los mismos
    atributos en otro orden tienen la misma huella."""
    atributos = sorted(json.dumps(a, sort_keys=True, ensure_ascii=False, default=str)
                       for a in (payload.get("attributes") or []))
    resto = {k: v for k, v in payload.items() if k != "attributes"}
    return json.dumps([resto, atributos], sort_keys=True, ensure_ascii=False, default=str)


# ── Lo que se le dice a la persona en la vista previa ────────────────────────

def aviso(guia: dict[str, Any]) -> str:
    """La línea de la vista previa para una cuenta. '' si el dominio no exige guía."""
    if guia.get("estado") == "ok":
        como = {"EU": " (por la columna EU de la guía"
                      + (f"; pie de {guia['pie']}" if guia.get("pie") else "") + ")",
                "MX": " (por la columna MX de la guía)",
                "letra": " (misma talla: la guía la escribe así)"}.get(guia.get("como"), "")
        if guia.get("a_mano"):
            # La Talla del formulario también puede venir de «Mejorar con IA»: solo elige
            # entre los renglones en los que la talla de WooCommerce YA cayó, y se dice.
            como += (" (renglón elegido en el Estudio entre los que le tocan; lo escrito ahí "
                     "puede venir de «Mejorar con IA»: revísalo)")
        linea = MSG_AVISO_OK.format(
            cuenta=guia["cuenta"], guia_id=guia["guia_id"], nombre=guia["guia_nombre"],
            talla=guia["talla"], renglon_id=guia["renglon_id"], size=guia["size"], como=como,
            genero=guia["genero"], origen_genero=guia["origen_genero"], marca=guia["marca"])
        if guia.get("talla_estudio"):
            linea += MSG_ESTUDIO_NO_USADO.format(escrita=guia["talla_estudio"])
        return linea
    if guia.get("estado") == "bloqueado":
        return MSG_AVISO_BLOQUEADO.format(cuenta=guia["cuenta"], motivo=guia["motivo"])
    return ""


def _nombre_de(payload: dict[str, Any], attr_id: str, category_id: str) -> str | None:
    """Cómo se LEE el atributo que trae un payload (su value_name, o el nombre de su
    value_id en la lista de la categoría ya leída). None si el payload no lo trae."""
    for a in payload.get("attributes") or []:
        if not isinstance(a, dict) or a.get("id") != attr_id:
            continue
        if str(a.get("value_name") or "").strip():
            return str(a["value_name"]).strip()
        with _candado:
            valores = ((_attrs_cat.get(category_id) or {}).get(attr_id) or {}).get("valores") or []
        for v in valores:
            if str(v.get("id")) == str(a.get("value_id")):
                return v["name"]
        return f"id {a.get('value_id')}"
    return None


def aviso_de_color(guia: dict[str, Any], del_vendor: dict[str, Any] | None) -> str:
    """La línea «Color: saldrá como «X»…» de la vista previa: de dónde salió, lo que el
    formulario del Estudio o la meta dicen y NO se usa, y —si el vendor habría mandado
    otro— cuál. `del_vendor` es el payload del dry-run ANTES de `aplicar`."""
    if guia.get("estado") != "ok":
        return ""
    cat = str(guia.get("category_id") or "")
    origen = guia.get("color_origen") or ""
    nota = str(guia.get("color_nota") or "")
    nota = f" {nota[0].upper()}{nota[1:]}." if nota else ""
    libre = "" if guia.get("color_id") else "; texto libre: no está en la lista de la categoría"
    antes = _nombre_de(del_vendor or {}, "COLOR", cat)
    vendor = ""
    if antes is not None and _plano(antes) != _plano(guia.get("color")):
        vendor = f" El publicador habría mandado «{antes}»."
    principal = ""
    previo = _nombre_de(del_vendor or {}, "MAIN_COLOR", cat)
    accion = (guia.get("main_color") or {}).get("accion")
    if previo is not None:
        if accion == "igualar":
            nuevo = guia["main_color"]["value_name"]
            if _plano(previo) != _plano(nuevo):
                principal = (f" El color principal (MAIN_COLOR) sale «{nuevo}»; el publicador "
                             f"habría mandado «{previo}».")
        elif accion == "quitar":
            principal = (f" El color principal (MAIN_COLOR) que habría mandado el publicador "
                         f"(«{previo}») no sale de este producto y se quita.")
        else:
            principal = (f" Ojo: el color principal (MAIN_COLOR) se queda como lo manda el "
                         f"publicador («{previo}»): la categoría lo exige y «{guia.get('color')}» "
                         "no está en su lista. Revísalo.")
    return MSG_COLOR.format(color=guia.get("color"), origen=origen, libre=libre, nota=nota,
                            vendor=vendor, principal=principal)


def motivo_moda_activa(pendientes: list[dict[str, Any]]) -> str:
    """El motivo del candado de pausa, con el estado REAL de cada publicación que se creó
    y no quedó pausada (`estado` None = no se pudo leer)."""
    lista = "; ".join(MSG_MODA_ITEM.format(
        item_id=p.get("item_id"), cuenta=p.get("cuenta"),
        estado=(MODA_ESTADO_LEIDO.format(estado=p["estado"]) if p.get("estado")
                else MODA_ESTADO_SIN_LEER)) for p in pendientes)
    return MSG_MODA_ACTIVA.format(lista=lista)


def motivo_conjunto(bloqueadas: list[dict[str, Any]],
                    sin_token: list[str] | tuple[str, ...] = ()) -> str:
    """El texto de la caja única de la vista previa cuando NINGUNA cuenta puede salir.
    Un faltante del producto se dice una vez; uno de la guía, por cuenta. Las cuentas que no
    se pudieron revisar por falta de token van DENTRO (con `ok: False` el modal solo pinta
    este texto: en `avisos` nadie las vería)."""
    cola = "".join(" " + MSG_SIN_TOKEN_EN_MOTIVO.format(cuenta=c) for c in sin_token)
    if not bloqueadas:
        return MSG_NO_SE_PUEDE + cola
    juntos = {tuple(g.get("codigos") or [g.get("codigo")]) for g in bloqueadas}
    if len(juntos) == 1 and set(next(iter(juntos))) <= CODIGOS_DEL_PRODUCTO:
        pistas = list(dict.fromkeys(g["pista"] for g in bloqueadas if g.get("pista")))
        return " ".join([MSG_NO_SE_PUEDE, bloqueadas[0].get("motivo_base") or "",
                         *pistas]).strip() + cola
    return MSG_NO_SE_PUEDE + " " + " — ".join(
        f"{g['cuenta']}: {g['motivo']}" for g in bloqueadas) + cola


def aviso_de_titulo(payload: dict[str, Any], guia: dict[str, Any]) -> str:
    """'' o el aviso de que el título (family_name) ya trae la talla o el color: ML los
    agrega solo (0 de 159 publicaciones vivas llevan la talla en el family_name). Desde
    v0.623.0 cada variante manda SU título, que suele traerlos."""
    titulo = _plano(payload.get("family_name"))
    if not titulo or guia.get("estado") != "ok":
        return ""
    trae: list[str] = []
    talla = _plano(guia.get("talla"))
    if talla and re.search(rf"(?<![0-9a-z]){re.escape(talla)}(?![0-9a-z])", titulo):
        trae.append(f"la talla («{guia.get('talla')}»)")
    for parte in re.split(r"[/,|]", str(guia.get("color") or "")):
        p = _plano(parte)
        if len(p) >= 3 and re.search(rf"(?<![0-9a-z]){re.escape(p)}(?![0-9a-z])", titulo):
            trae.append(f"el color («{parte.strip()}»)")
            break
    return MSG_TITULO.format(que=" y ".join(trae)) if trae else ""


# ── El mensaje de un rechazo de ML, con las causas que SÍ son error ──────────

def causas_error(respuesta: Any) -> list[dict[str, Any]]:
    """Las causas `type == "error"` de una respuesta de ML. Una causa sin `type` cuenta
    como error. ML manda `cause` como lista, dict suelto o hasta un entero (EST-0091)."""
    causas = respuesta.get("cause") if isinstance(respuesta, dict) else None
    if isinstance(causas, dict):
        causas = [causas]
    if not isinstance(causas, (list, tuple)):
        return []
    return [c for c in causas
            if isinstance(c, dict) and str(c.get("type") or "error").lower() == "error"]


def _es_de_guia(causa: dict[str, Any]) -> bool:
    codigo, mensaje = str(causa.get("code") or ""), str(causa.get("message") or "")
    return ".fashion_grid." in codigo or (codigo == "item.attribute.invalid"
                                          and "SIZE_GRID" in mensaje)


def _es_gtin(causa: dict[str, Any]) -> bool:
    """El mismo criterio del vendor (publisher_core.py:623-626), sobre UNA causa."""
    codigo, mensaje = str(causa.get("code") or ""), str(causa.get("message") or "")
    return ("product_identifier.invalid_format" in codigo
            or (codigo == "item.attribute.missing_conditional_required" and "GTIN" in mensaje)
            or ("GTIN" in mensaje and "invalid" in mensaje.lower()))


def es_rechazo_de_guia(resultado: dict[str, Any]) -> bool:
    """¿ML rechazó por la guía? Es determinista: reintentar no lo arregla."""
    return any(_es_de_guia(c) for c in causas_error((resultado or {}).get("ml_response")))


def es_rechazo_definitivo(resultado: dict[str, Any]) -> bool:
    """HTTP 400 con al menos una causa de tipo error: el mismo envío fallará igual."""
    r = resultado or {}
    return r.get("ml_status") == 400 and bool(causas_error(r.get("ml_response")))


def etiqueta_error(resultado: dict[str, Any], guia: dict[str, Any] | None,
                   cuenta: str) -> str | None:
    """
    El mensaje de un rechazo de ML armado SOLO con sus causas `type == "error"`, o None si
    no hay nada mejor que decir (la etiqueta del vendor se queda).

    El vendor arma la suya recorriendo TODAS las causas (publisher_core.py:629-660): un
    warning de envío tapaba el error real de la guía (CALZ-0182, 19-jun) y el del código de
    barras tapaba al de la guía en 88 intentos.
    """
    errores = causas_error((resultado or {}).get("ml_response"))
    if not errores:
        return None
    g = guia or {}
    razones: list[str] = []
    for causa in errores:
        codigo = str(causa.get("code") or "")
        mensaje = str(causa.get("message") or "").strip()[:200]
        if _es_de_guia(causa):
            if g.get("estado") == "ok":
                razon = RAZON_GUIA_ENVIADA.format(
                    code=codigo, mensaje=mensaje, guia_id=g["guia_id"],
                    renglon_id=g["renglon_id"], size=g["size"], genero=g["genero"],
                    color=g.get("color") or "", cuenta=cuenta)
            elif g.get("estado") == "desconocido":
                razon = RAZON_GUIA_SIN_RESOLVER.format(code=codigo, porque=(
                    f"no se pudo leer la categoría {g.get('category_id') or '?'} para "
                    f"resolverla ({g.get('motivo') or 'sin detalle'}). Vuelve a intentar"))
            else:
                razon = RAZON_GUIA_SIN_RESOLVER.format(code=codigo, porque=(
                    f"el dominio {g.get('dominio') or '(desconocido)'} no está en la lista "
                    "de dominios con guía del panel (DOMINIOS_CON_GUIA en "
                    "services/ml_guia_tallas.py). Hay que agregarlo"))
        elif _es_gtin(causa):
            razon = f"GTIN_INVALIDO: cuenta {cuenta} requiere código de barras real"
        elif codigo in _RAZONES_VENDOR:
            razon = _RAZONES_VENDOR[codigo]
        else:
            razon = f"{codigo}: {mensaje}" if codigo else mensaje
        if razon and razon not in razones:
            razones.append(razon)
    if not razones:
        return None
    return ("Mercado Libre rechazó el alta — " + " | ".join(razones))[:1200]
