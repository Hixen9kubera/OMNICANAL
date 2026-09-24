"""
odoo_notas_combinado.py — "Esta orden viaja en la misma caja que…", en Odoo.

LO QUE PIDIÓ BRANDON (23-sep-2026)
──────────────────────────────────
    "¿hay forma de poner una nota a las órdenes con guías combinadas de Temu
     en Odoo?" … "dale con las notas, me interesa más que se encuentre en la
     ORDEN DE VENTA, que indique CON QUÉ ÓRDENES DE VENTA está combinada."

QUÉ ES UN ENVÍO COMBINADO, Y QUÉ ES UN SURTIDO DIVIDIDO
───────────────────────────────────────────────────────
Los dos casos acaban igual —varias ÓRDENES DE VENTA de Odoo cuya ENTREGA de
salida comparte guía, o sea UNA caja— pero NO se llaman igual, y el nombre
importa porque el almacén ya tiene otras dos vistas delante:

· **ENVÍO COMBINADO** = dos o más VENTAS distintas del canal en la misma caja.
  Temu junta las compras del mismo comprador a la misma dirección.
· **SURTIDO DIVIDIDO** = UNA venta que ningún almacén tenía completa y nació en
  dos órdenes (refs `<venta>#1` / `<venta>#2`), que acabaron saliendo juntas.

`guias_del_dia.py` (el Excel) y `frontend/lib/combinados.ts` (la pestaña) ya
definen "combinado" contando VENTAS distintas, y dicen con todas sus letras que
un surtido dividido NO es combinado. Si aquí se llamara "ENVÍO COMBINADO" al
surtido dividido, quien cotejara el Excel contra Odoo encontraría un aviso que
el Excel niega. Por eso `texto_bloque` mira cuántas VENTAS hay —no cuántas
órdenes— y encabeza con una cosa o la otra. Por lo mismo el conteo se dice sin
ambigüedad ("6 órdenes de venta (4 ventas)"): la pestaña cuenta ventas, el
Excel cuenta órdenes, y tres números distintos para la misma caja no ayudan.

Medido el 23-sep-2026 en Odoo (21 días, partner de Temu): 95 órdenes y 6 grupos
combinados — uno de ellos con SEIS órdenes en una sola guía. TikTok tenía 0 ese
día, pero el código no distingue: el canal se pasa por parámetro.

EL ALMACÉN VA EN EL TEXTO
─────────────────────────
Los grupos de hoy cruzan TEXCO y TEXCO II. "Empaca todo junto e imprime una
sola vez" no le dice a NADIE quién junta la caja si las hermanas están en
bodegas distintas: el riesgo pasa a ser el simétrico —que nadie imprima, o que
salgan dos bultos y uno sin etiqueta—. El dato ya venía en memoria (`_leer`
pide el `name` de la entrega y el prefijo lo dice: `TEXCO/` vs `TEX2/`), así
que cada hermana se nombra con su almacén y la propia orden también.

DÓNDE SE ESCRIBE, Y POR QUÉ AHÍ
───────────────────────────────
· `sale.order.note` (html, etiqueta "Terms and conditions") — la nota que el
  almacén ya ve en la orden. **LAS 95 ÓRDENES YA TRAEN TEXTO** (lo pone
  `crear_orden`: "Creada automáticamente desde Temu…"). Por eso NUNCA se pisa:
  el bloque nuestro va al PRINCIPIO, entre dos marcas HTML propias, y al
  actualizar se reemplaza SÓLO lo que está entre las marcas.

  ⚠️ **ESE CAMPO ACABA EN LA FACTURA.** `sale.order.note` es "Terms and
  conditions", y al facturar Odoo lo copia a `account.move.narration`: el día
  que alguien facture una de estas órdenes, la factura nacerá con el aviso de
  almacén en sus condiciones. Hoy no hay ninguna facturada (0 de 120 miradas el
  23-sep; 37 están "to invoice"), y se deja así A SABIENDAS porque es el campo
  que pidió el encargo y el que el almacén tiene delante. Queda escrito para
  que sea una decisión y no una sorpresa. Si algún día molesta, la salida es
  dejar el aviso sólo en `stock.picking.note` —que imprime en "Picking
  Operations" y "Delivery Slip"— y en el historial, y filtrar por la etiqueta.
· `sale.order.tag_ids` → una `crm.tag` llamada "ENVÍO COMBINADO", para poder
  filtrar. Se busca por nombre exacto y sólo se crea si no existe; las
  etiquetas del equipo ("(ZONA 1) Tamaño S", "First_Order"…) no se tocan jamás
  — se usa el comando 4 (enlazar), nunca el 6 (reemplazar la lista entera).
· `stock.picking.note` de la entrega de salida, que es lo que tiene delante
  quien empaca. Estaba VACÍA en todas las entregas de estas órdenes.
· `message_post` en el historial, y SÓLO cuando el bloque nace o cambia: un
  mensaje por vuelta convertiría en ruido, cada 15 minutos, justo el lugar
  donde se va a buscar qué pasó.

LAS REGLAS QUE ESTO OBEDECE
───────────────────────────
1. **Respeta los interruptores**, igual que `fijar_guia` y `fijar_etiqueta`:
   `habilitado()` —el botón de pánico del panel— y `canal_activo(canal)`, más
   su propia bandera `ODOO_VENTAS_NOTAS_COMBINADO_ENABLED`. Apagado = no
   escribe NADA.
2. **No escribe si el texto no cambió.** La comparación es del BLOQUE
   normalizado, no de la nota entera: Odoo reacomoda el html al guardarlo, y
   comparar el crudo haría reescribir —y publicar en el historial— en cada
   vuelta.
3. **Las canceladas sólo se LIMPIAN, nunca se marcan.** Y se leen aparte
   justamente para poder limpiarlas: una orden que se cancela DESPUÉS de haber
   quedado marcada conservaría el aviso para siempre si el dominio de lectura
   la dejara fuera, y filtrar por la etiqueta sacaría mercancía que ya no viaja
   con nadie.
4. **Se RE-LEE, ANTES Y DESPUÉS.** Antes de escribir, porque entre la foto del
   principio de la vuelta y el `write` pasan decenas de viajes XML-RPC y en ese
   rato alguien del almacén pudo escribir en los Términos ("entregar en la
   puerta 3"): ese valor gana siempre, igual que en `fijar_guia`. Y después,
   porque un `write` que contesta bien no prueba que el dato quedó.
5. **Limpieza**: una orden que trae nuestro bloque o nuestra etiqueta y ya NO
   está combinada —cambió de guía, la hermana se canceló— los pierde. Un aviso
   que dejó de ser cierto manda a empacar mal con la misma confianza que uno
   bueno. Sólo se retira LO NUESTRO. **Excepción: si la caja YA SALIÓ** (todas
   sus entregas en `done`) el aviso se queda: a los 21 días la hermana envejece
   y sale de la ventana, y retirarle el aviso a la que queda borraría
   justamente el registro que pidió Brandon para afirmar, encima, que "viaja
   sola" — que es falso, la caja se fue hace semanas con las dos dentro.
6. **Sin datos del comprador** en ningún texto ni en ningún log: nombres de
   orden de Odoo y número de guía, nada más.
7. **Nunca lanza.** La llama un job del scheduler; un fallo suyo no puede
   tumbar nada. Los errores se cuentan y se devuelven.

EL SANEADOR SE COME LAS MARCAS — MEDIDO EN VIVO EL 23-SEP-2026
──────────────────────────────────────────────────────────────
⚠️ **No es una hipótesis: pasó.** En la primera corrida real (27 órdenes de
Temu, 02:31 UTC) Odoo guardó las notas SIN los comentarios html: las 27 se
reconocen hoy por el CINTURÓN, o sea por el texto visible del aviso. Las marcas
`<!-- OMNICANAL:COMBINADO -->` quedan como adorno: se siguen mandando —si algún
día el saneador las respetara, mejor— pero **quien sostiene la idempotencia es
la frase**, no el comentario. Cambiar el texto del título o de la última frase
(`SELLO_VISIBLE`) dejaría huérfanos los 27 bloques ya escritos, y la vuelta
siguiente apilaría uno nuevo encima. Si hay que cambiarlo: primero se limpian
los bloques viejos, o se amplía `_RE_VISIBLE` para reconocer también el
anterior.

Segunda vuelta (02:46 UTC): 0 escrituras, 0 apuntes, 4 segundos. Verificado
leyendo Odoo: 1 aviso por nota, 1 por entrega, 1 apunte por historial, y los
Términos previos intactos en las 27.

Toda la idempotencia cuelga, entonces, de reconocer lo ya escrito. El campo
`sale.order.note` es `sanitize=True` (medido con `fields_get`) y el saneador
reformatea: el texto plano de `crear_orden` está guardado como `<p>…</p>`.

Sin nada que reconocer, `bloque_actual` devolvería None en la vuelta siguiente y
`con_bloque` ANTEPONDRÍA un párrafo nuevo sobre el anterior: medido, la nota
crece ~158 caracteres por vuelta y el historial suma 2 mensajes por vuelta. Con
el job cada 15 min sobre 27 órdenes eso es ~2,600 escrituras y ~5,200 mensajes
AL DÍA, y limpiarlo es a mano, orden por orden. Eso es exactamente lo que habría
pasado aquí sin el cinturón: el saneador se las comió el primer día.

Por eso hay DOS defensas, y ninguna depende de adivinar qué hace el saneador:

1. EL CINTURÓN. El bloque se reconoce por las marcas y, si no aparecen, por el
   aviso VISIBLE (`<p><b>ENVÍO COMBINADO</b> … Imprimir la etiqueta UNA sola
   vez.</p>`), que es contenido y no se lo puede comer nadie. Con eso, un
   saneador que borre los comentarios sigue produciendo el comportamiento
   correcto: el bloque se REEMPLAZA, no se apila. Por la misma razón `_aplanar`
   quita las marcas antes de comparar: si estuvieran dentro de la comparación,
   "no se escribe si no cambió" sería falso para siempre.
2. EL FRENO. El dato que ya se calculaba —la re-lectura de verificación— ahora
   detiene en vez de sólo contarse: a la primera nota que no vuelve reconocible
   (ni por marca ni por texto), se levanta un candado de módulo (`_DETENIDO`),
   se deja de escribir en esa vuelta Y EN LAS SIGUIENTES, y se registra un
   ERROR. Falla CERRADO: el daño máximo es UNA orden. Se suelta con
   `reanudar()` o reiniciando el contenedor.

El canario —escribir el bloque en UNA orden en vivo y releerla— sigue siendo la
forma de saber qué hace el saneador de verdad; con el cinturón y el freno ya no
es un requisito para encender, sino la confirmación de por cuál de los dos
caminos está funcionando.

TODO ESTO BLOQUEA (regla 11)
────────────────────────────
XML-RPC es síncrono. `notar_combinados` se llama desde `asyncio.to_thread`;
llamarla dentro de una corrutina detiene el backend ENTERO mientras Odoo
contesta, no sólo a quien llamó.
"""
from __future__ import annotations

import html as _html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("omnicanal.odoo_notas_combinado")

# Las marcas. Son comentarios HTML: invisibles en la ficha de Odoo y la forma
# limpia de saber qué trozo de la nota es nuestro. ⚠️ EL SANEADOR DE ESTE ODOO
# SE LAS COME (medido el 23-sep-2026, las 27 órdenes): se siguen mandando por si
# algún día sobrevivieran, pero quien reconoce el bloque en la práctica es
# `SELLO_VISIBLE`, abajo. Cambiarlas ya no deja huérfano nada; cambiar el TEXTO
# del aviso, sí.
MARCA_INI = "<!-- OMNICANAL:COMBINADO -->"
MARCA_FIN = "<!-- /OMNICANAL:COMBINADO -->"
_RE_BLOQUE = re.compile(re.escape(MARCA_INI) + r".*?" + re.escape(MARCA_FIN), re.S)

# EL CINTURÓN, por si el saneador de Odoo se comiera los comentarios (el campo
# es `sanitize=True` y ese caso no se ha podido probar en vivo): el aviso
# también se reconoce por lo que se VE — su título en negritas y su última
# frase—, que es contenido y por lo tanto nada se lo puede comer. Así, en el
# peor caso el bloque se REEMPLAZA igual en vez de apilarse, y el freno sólo
# salta si no vuelve ni el comentario ni el texto.
SELLO_VISIBLE = "Imprimir la etiqueta UNA sola vez"
_RE_VISIBLE = re.compile(
    r"(?:<p[^>]*>\s*<(?:b|strong)>\s*(?:ENVÍO COMBINADO|SURTIDO DIVIDIDO)\s*"
    r"</(?:b|strong)>.*?" + re.escape(SELLO_VISIBLE) + r"\.?\s*</p>)+", re.S)

# Lo que se busca en Odoo para encontrar lo NUESTRO sin depender del html
# completo: el trozo estable de la marca, que sirve en un dominio `like`.
SELLO = "OMNICANAL:COMBINADO"

# El nombre EXACTO de la etiqueta: así se busca, y sólo se crea si no aparece.
TAG_NOMBRE = "ENVÍO COMBINADO"


# ── El candado de "las marcas no volvieron" ─────────────────────────────────
# Es de MÓDULO (no por corrida) a propósito: el daño que evita —la nota
# creciendo cada 15 minutos— se acumula entre vueltas, así que un freno que se
# olvidara al terminar la corrida no frenaría nada.
_DETENIDO: dict[str, Any] = {"motivo": None, "cuando": None, "donde": None}


def detenido() -> dict[str, Any]:
    """Copia del candado: `{"motivo", "cuando", "donde"}`. `motivo` None = libre."""
    return dict(_DETENIDO)


def reanudar() -> None:
    """Suelta el candado. Se llama a mano, después de entender por qué saltó."""
    _DETENIDO.update({"motivo": None, "cuando": None, "donde": None})


def _detener(motivo: str, donde: Any) -> None:
    if _DETENIDO["motivo"]:
        return
    _DETENIDO.update({"motivo": motivo, "donde": str(donde),
                      "cuando": datetime.now(timezone.utc).isoformat()})
    log.error("NOTAS COMBINADAS DETENIDAS: %s (en %s). No se escribirá nada más "
              "hasta reiniciar o llamar a reanudar(). Lo más probable es que el "
              "saneador de html de Odoo se esté comiendo las marcas %s: hay que "
              "mirar la nota de esa orden ANTES de volver a encender.",
              motivo, donde, MARCA_INI)


# ── Texto ───────────────────────────────────────────────────────────────────

def _normalizar_guia(guia: Any) -> str:
    """La llave con la que se agrupa. Sin espacios y en mayúsculas: la misma
    guía escrita "JMX 601…" y "jmx601…" es la misma caja."""
    return "".join(str(guia or "").split()).upper()


def _aplanar(texto: Any) -> str:
    """Espacios colapsados, para comparar sin depender del formato.

    Las marcas se quitan ANTES de comparar: si el saneador se las comiera, el
    bloque que devuelve Odoo y el que se acaba de armar dirían lo mismo y sólo
    se diferenciarían en el comentario. Compararlos con las marcas dentro haría
    que "no se escribe si no cambió" fuera falso para siempre.
    """
    t = str(texto or "").replace(MARCA_INI, " ").replace(MARCA_FIN, " ")
    return " ".join(t.split())


def codigo_guia(guia: Any) -> str:
    """"…1023": el nombre corto con el que el Excel y la pestaña llaman a la
    caja. Se dice también aquí para que las tres vistas se nombren igual."""
    g = _normalizar_guia(guia)
    return f"…{g[-4:]}" if g else ""


def venta_de(ref: Any) -> str:
    """La VENTA del canal detrás de una `client_order_ref`.

    Un surtido dividido lleva sufijo (`<venta>#1`, `<venta>#2`): las dos partes
    son la MISMA venta. Es el mismo corte que hace `odoo_ventas` en cuatro
    sitios, y el que decide si esto se llama combinado o dividido.
    """
    return str(ref or "").partition("#")[0].strip()


def almacen_de(nombre_entrega: Any) -> str:
    """"TEXCO" / "TEXCO II" a partir del nombre de la entrega.

    El prefijo del albarán es el código del almacén (`TEXCO/OUT/06414`,
    `TEX2/OUT/00063`). Cualquier otro prefijo se devuelve tal cual: es
    preferible decir un código raro que callar el dato.
    """
    pref = str(nombre_entrega or "").split("/", 1)[0].strip().upper()
    return {"TEX2": "TEXCO II"}.get(pref, pref)


def _nombre_hermana(h: Any) -> str:
    """Una hermana se acepta como texto ("S39008") o como
    `{"nombre", "almacen"}`. Lo primero mantiene `texto_bloque` usable a secas."""
    if isinstance(h, dict):
        nombre = _html.escape(str(h.get("nombre") or ""))
        alm = str(h.get("almacen") or "")
        return f"{nombre} ({_html.escape(alm)})" if alm else nombre
    return _html.escape(str(h))


def _frase_grupo(g: dict[str, Any]) -> tuple[str, str, str]:
    """(título, verbo, conteo) de UN grupo. PURA.

    El único sitio donde se decide si esto se llama COMBINADO o DIVIDIDO, para
    que la nota, el albarán y el historial no se contradigan entre sí.
    """
    total = int(g["total"])
    ventas = int(g.get("ventas") or total)
    if ventas >= 2:
        # "6 órdenes de venta (4 ventas)": el Excel cuenta órdenes y la pestaña
        # cuenta ventas. Decir los dos números evita que la misma caja tenga
        # tres cifras distintas según dónde se mire.
        return ("ENVÍO COMBINADO", "viaja en la MISMA caja que:",
                f"{total} órdenes de venta ({ventas} ventas)"
                if ventas != total else f"{total} órdenes de venta")
    return ("SURTIDO DIVIDIDO",
            "es otra parte de la MISMA venta y va en la MISMA caja que:",
            f"{total} partes en total")


def texto_bloque(grupos: list[dict[str, Any]], sujeto: str = "Esta orden") -> str:
    """
    El html del bloque, marcas incluidas. PURA.

    `grupos` = [{"guia", "hermanas", "total", "ventas"}], uno por guía.
    Normalmente es UNO. Son varios sólo si la misma orden tiene dos entregas de
    salida con guías distintas y cada una viaja acompañada: ahí se dice una vez
    por caja, en vez de elegir una y callar la otra.

    `ventas` decide el ENCABEZADO (ver el porqué arriba): 2 o más ventas es un
    ENVÍO COMBINADO; una sola venta partida es un SURTIDO DIVIDIDO, que es como
    lo llaman el Excel y la pestaña. Si no viene, se asume combinado.

    `sujeto` es quién habla: "Esta orden (S39009, TEXCO)" en la orden y "Esta
    entrega (TEXCO/OUT/06415)" en el albarán — donde "esta orden" no tendría
    referente.
    """
    parrafos = []
    for g in grupos:
        hermanas = ", ".join(_nombre_hermana(h) for h in g["hermanas"])
        titulo, verbo, cuantas = _frase_grupo(g)
        guia = _html.escape(str(g["guia"]))
        corto = codigo_guia(g["guia"])
        parrafos.append(
            f"<p><b>{titulo}</b> · {_html.escape(sujeto)} {verbo} "
            f"{hermanas} · {cuantas} · "
            f"Guía {guia}{f' ({_html.escape(corto)})' if corto else ''} · "
            "Imprimir la etiqueta UNA sola vez.</p>")
    return f"{MARCA_INI}{''.join(parrafos)}{MARCA_FIN}"


def bloque_actual(nota: Any) -> str | None:
    """El bloque nuestro que ya trae la nota, o None si no hay ninguno.

    Primero por las marcas; si no aparecen —saneador—, por el aviso visible.
    """
    texto = str(nota or "")
    m = _RE_BLOQUE.search(texto) or _RE_VISIBLE.search(texto)
    return m.group(0) if m else None


def sin_bloque(nota: Any) -> str:
    """La nota SIN nuestro bloque — o sea, lo que escribió alguien más.

    Se quita el bloque y NADA MÁS: ni un espacio de más. Lo que había sigue
    ahí, carácter por carácter, incluido el salto de línea con el que empezaba.
    Por eso `con_bloque` tampoco mete separador: si uno pusiera "\\n" y el otro
    hiciera `lstrip`, poner y quitar el bloque le comería a la nota original el
    espacio con el que nació — y "no se escribe si no cambió" dejaría de ser
    cierto en el primer paso.
    """
    return _RE_VISIBLE.sub("", _RE_BLOQUE.sub("", str(nota or "")))


def con_bloque(nota: Any, bloque: str) -> str:
    """La nota con nuestro bloque al PRINCIPIO y lo demás detrás, intacto."""
    return f"{bloque}{sin_bloque(nota)}"


def _igual(bloque_a: Any, bloque_b: Any) -> bool:
    return _aplanar(bloque_a) == _aplanar(bloque_b)


# ── Agrupar por guía ────────────────────────────────────────────────────────

def agrupar(ordenes: list[dict[str, Any]],
            entregas: dict[int, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """
    { guía_normalizada: {"guia", "ordenes": {sale_id: [picking_id, …]}, "ventas"} }
    con SÓLO los grupos de verdad: dos o más ÓRDENES DE VENTA distintas. PURA.

    `entregas` viene llaveada por id. Las CANCELADAS se leen —hay que poder
    limpiarlas— pero aquí se saltan: una entrega cancelada no va en ninguna
    caja, y agruparla inventaría combinaciones que no existen.

    Las dos mitades de un surtido dividido (`venta#1` y `venta#2`) son DOS
    órdenes de venta, así que cuentan: es justo el caso en que el almacén tiene
    dos documentos delante y una sola caja. Se cuentan aparte las VENTAS
    distintas, porque de eso depende cómo se llama el aviso.
    """
    grupos: dict[str, dict[str, Any]] = {}
    for o in ordenes:
        oid = int(o["id"])
        venta = venta_de(o.get("client_order_ref")) or f"#{oid}"
        for pid in (o.get("picking_ids") or []):
            p = entregas.get(int(pid))
            if not p or p.get("state") == "cancel":
                continue
            guia = str(p.get("carrier_tracking_ref") or "").strip()
            clave = _normalizar_guia(guia)
            if not clave:
                continue
            g = grupos.setdefault(clave, {"guia": guia, "ordenes": {}, "ventas": set()})
            g["ordenes"].setdefault(oid, []).append(int(pid))
            g["ventas"].add(venta)
    return {k: {**g, "ventas": len(g["ventas"])}
            for k, g in grupos.items() if len(g["ordenes"]) >= 2}


def grupos_por_orden(grupos: dict[str, dict[str, Any]],
                     nombre: dict[int, str],
                     entregas: dict[int, dict[str, Any]] | None = None,
                     ) -> dict[int, list[dict[str, Any]]]:
    """{sale_id: [{"guia", "hermanas", "total", "ventas", "pickings", "almacen"}]},
    listo para el texto. PURA.

    Las HERMANAS son las OTRAS órdenes del grupo, nunca ella misma: una nota
    que se cita a sí misma se lee como un error de programa y le quita crédito
    a todo el aviso. Cada una viene con SU almacén, sacado del nombre de la
    entrega con la que entró a ESTE grupo.
    """
    entregas = entregas or {}

    def _alm(oid: int, g: dict[str, Any]) -> str:
        for pid in sorted(g["ordenes"].get(oid) or []):
            p = entregas.get(int(pid))
            if p and p.get("name"):
                return almacen_de(p["name"])
        return ""

    salida: dict[int, list[dict[str, Any]]] = {}
    for clave in sorted(grupos, key=lambda k: grupos[k]["guia"]):
        g = grupos[clave]
        ids = sorted(g["ordenes"])
        for oid in ids:
            hermanas = sorted(
                ({"nombre": nombre.get(x) or str(x), "almacen": _alm(x, g)}
                 for x in ids if x != oid),
                key=lambda h: h["nombre"])
            salida.setdefault(oid, []).append({
                "guia": g["guia"], "hermanas": hermanas, "total": len(ids),
                "ventas": int(g.get("ventas") or len(ids)),
                "pickings": sorted(g["ordenes"][oid]), "almacen": _alm(oid, g),
            })
    return salida


def _sujeto_orden(o: dict[str, Any], grupos: list[dict[str, Any]]) -> str:
    """"Esta orden (S39009, TEXCO)" — quién es la que habla."""
    nombre = str(o.get("name") or "").strip()
    alm = next((str(g.get("almacen") or "") for g in grupos if g.get("almacen")), "")
    dentro = ", ".join(x for x in (nombre, alm) if x)
    return f"Esta orden ({dentro})" if dentro else "Esta orden"


# ── El trabajo ──────────────────────────────────────────────────────────────

def notar_combinados(canal: str, dias: int = 21, limite: int = 300,
                     dry_run: bool = False) -> dict[str, Any]:
    """
    Escribe (y retira) la nota de envío combinado en Odoo. ⚠️ BLOQUEA.

    Devuelve un resumen —grupos, ordenes, notas_escritas, notas_iguales,
    etiquetas, limpiadas, errores, verificadas—, el `detalle` de cada grupo con
    su guía y sus órdenes, y `planeado`: TODO lo que se iba a escribir, en
    orden. NUNCA lanza.

    `dry_run=True` calcula todo y no escribe nada, pase lo que pase con las
    banderas Y CON EL CANDADO — es lo que hace seguro mirar antes de encender,
    y lo que permite seguir mirando después de que el candado saltara. Es la
    misma decisión que ya tomó `crear_orden`: sin un dry_run que no dependa de
    los flags, "simular" deja de simular en cuanto alguien mueve un interruptor.
    """
    from config import settings
    from services import odoo_ventas

    canal = (canal or "").lower()
    r: dict[str, Any] = {
        "canal": canal, "grupos": 0, "ordenes": 0, "notas_escritas": 0,
        "notas_iguales": 0, "etiquetas": 0, "entregas_escritas": 0,
        "mensajes": 0, "limpiadas": 0, "verificadas": 0, "no_verificadas": 0,
        "errores": 0, "detalle": [], "planeado": [], "dry_run": bool(dry_run),
    }
    partner = odoo_ventas._PARTNER.get(canal)
    if not partner:
        return {**r, "accion": "canal_desconocido",
                "motivo": f"canal '{canal}' sin partner configurado"}

    if not dry_run:
        # EL CANDADO VA PRIMERO: si las marcas no volvieron, no se vuelve a
        # intentar lo mismo cada 15 minutos. Ver el encabezado.
        if _DETENIDO["motivo"]:
            return {**r, "accion": "detenido",
                    "motivo": f"detenido: {_DETENIDO['motivo']} "
                              f"(en {_DETENIDO['donde']}, {_DETENIDO['cuando']})"}
        # Tres interruptores, y el orden importa: primero el propio —apagar las
        # notas no debería exigir apagar la creación de órdenes—, luego el
        # general, que es el botón de pánico del panel, y al final el del canal.
        if not bool(getattr(settings, "odoo_ventas_notas_combinado_enabled", False)):
            return {**r, "accion": "apagado_notas",
                    "motivo": "ODOO_VENTAS_NOTAS_COMBINADO_ENABLED está apagado"}
        try:
            if not odoo_ventas.habilitado():
                return {**r, "accion": "apagado",
                        "motivo": "el interruptor general de órdenes en Odoo está apagado"}
            if not odoo_ventas.canal_activo(canal):
                return {**r, "accion": "canal_apagado",
                        "motivo": f"el canal {canal} está apagado"}
        except Exception as exc:  # noqa: BLE001 — leer un flag no tumba el job
            log.warning("notas combinadas %s: no se pudieron leer los "
                        "interruptores (%s)", canal, str(exc)[:150])
            return {**r, "accion": "error", "errores": 1,
                    "motivo": f"interruptores: {str(exc)[:200]}"}

    kw = odoo_ventas._kw
    try:
        ordenes, canceladas, entregas = _leer(kw, partner, dias, limite)
    except Exception as exc:  # noqa: BLE001 — Odoo caído no tumba el scheduler
        log.warning("notas combinadas %s: Odoo no contestó: %s", canal, str(exc)[:200])
        return {**r, "accion": "error", "errores": 1, "motivo": f"odoo: {str(exc)[:200]}"}

    nombre = {int(o["id"]): str(o.get("name") or "")
              for o in list(ordenes) + list(canceladas)}
    grupos = agrupar(ordenes, entregas)
    por_orden = grupos_por_orden(grupos, nombre, entregas)
    r["grupos"] = len(grupos)
    r["ordenes"] = len(por_orden)
    r["canceladas"] = len(canceladas)
    r["detalle"] = [
        {"guia": grupos[k]["guia"], "total": len(grupos[k]["ordenes"]),
         "ventas": grupos[k].get("ventas"),
         "ordenes": sorted(nombre.get(i) or str(i) for i in grupos[k]["ordenes"])}
        for k in sorted(grupos, key=lambda k: grupos[k]["guia"])
    ]

    estado: dict[str, Any] = {"tag": None, "buscada": False, "resuelta": False,
                              "envenenado": False}
    # Las CANCELADAS van sólo por el camino de la limpieza: nunca se marcan.
    for o in list(ordenes) + [{**c, "_cancelada": True} for c in canceladas]:
        if estado["envenenado"]:
            break
        oid = int(o["id"])
        try:
            if oid in por_orden and not o.get("_cancelada"):
                _marcar(kw, r, estado, o, por_orden[oid], entregas, dry_run)
            else:
                _limpiar(kw, r, estado, o, entregas, dias, dry_run)
        except Exception as exc:  # noqa: BLE001 — una orden mala no se lleva al resto
            r["errores"] += 1
            log.warning("notas combinadas %s: la orden %s falló: %s",
                        canal, nombre.get(oid) or oid, str(exc)[:200])

    if r["notas_escritas"] or r["limpiadas"] or r["etiquetas"] or r["errores"]:
        log.info("Notas de envío combinado %s: %s grupo(s), %s orden(es) — "
                 "%s nota(s) escrita(s), %s igual(es), %s etiqueta(s), "
                 "%s entrega(s), %s limpiada(s), %s verificada(s), %s error(es)%s",
                 canal, r["grupos"], r["ordenes"], r["notas_escritas"],
                 r["notas_iguales"], r["etiquetas"], r["entregas_escritas"],
                 r["limpiadas"], r["verificadas"], r["errores"],
                 " [DRY RUN]" if dry_run else "")
    if estado["envenenado"]:
        r["accion"] = "detenido"
        r["motivo"] = _DETENIDO["motivo"]
    else:
        r["accion"] = "simulado" if dry_run else "hecho"
    return r


def _leer(kw: Any, partner: int, dias: int, limite: int) -> tuple[
        list[dict[str, Any]], list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """Las órdenes vivas del canal, las CANCELADAS que traen lo nuestro, y las
    entregas de salida de unas y otras. ⚠️ BLOQUEA.

    Tres consultas, sin importar cuántas órdenes haya.

    POR QUÉ SE LEEN LAS CANCELADAS: sólo las que traen el SELLO, que son las
    que hay que limpiar. Si el dominio las dejara fuera —como hacía antes—, una
    orden marcada y cancelada después conservaría "viaja en la MISMA caja que
    S39022" y la etiqueta para siempre, porque la limpieza sólo alcanza a lo
    que la lectura trae. Nunca entran a `agrupar`.

    SÓLO ENTREGAS DE SALIDA, por la misma razón que en `pendientes_de_guia`: el
    rastreo de una transferencia interna o de una devolución no dice nada de la
    caja que sale, y agrupar por él inventaría combinaciones que no existen.
    Las canceladas SÍ se traen (con su `state`) para poder limpiarlas; `agrupar`
    las descarta.

    EL BORDE DE LA VENTANA: lo que queda fuera de `dias` (o del `limite`) no
    existe para esta función. Si una hermana envejece y sale de la ventana antes
    que la otra, la que queda se vería sola — por eso `_limpiar` NO retira el
    aviso de una orden cuya caja ya salió (todas sus entregas en `done`).
    """
    desde = (datetime.now(timezone.utc) - timedelta(days=int(dias))
             ).strftime("%Y-%m-%d %H:%M:%S")
    campos = ["name", "client_order_ref", "state", "note", "tag_ids", "picking_ids"]
    ordenes = kw("sale.order", "search_read",
                 [[["partner_id", "=", partner],
                   ["client_order_ref", "!=", False],
                   ["state", "!=", "cancel"],
                   ["create_date", ">=", desde]]],
                 {"fields": campos, "order": "create_date desc",
                  "limit": int(limite)}) or []
    # Las canceladas NO se acotan por fecha: una marcada hace dos meses y
    # cancelada ayer sigue mintiendo hoy, y son pocas por definición (sólo las
    # que llevan el sello).
    canceladas = kw("sale.order", "search_read",
                    [[["partner_id", "=", partner],
                      ["state", "=", "cancel"],
                      # Por el sello O por el aviso visible: si el saneador se
                      # comiera las marcas, buscar sólo el sello no encontraría
                      # nada que limpiar.
                      "|",
                      ["note", "like", SELLO],
                      ["note", "like", SELLO_VISIBLE]]],
                    {"fields": campos, "limit": int(limite)}) or []
    ids = sorted({int(i) for o in list(ordenes) + list(canceladas)
                  for i in (o.get("picking_ids") or [])})
    entregas: dict[int, dict[str, Any]] = {}
    if ids:
        for p in kw("stock.picking", "search_read",
                    [[["id", "in", ids],
                      ["picking_type_code", "=", "outgoing"]]],
                    {"fields": ["name", "state", "carrier_tracking_ref",
                                "note"]}) or []:
            entregas[int(p["id"])] = p
    return ordenes, canceladas, entregas


def _tag(kw: Any, estado: dict[str, Any], r: dict[str, Any],
         crear: bool, dry_run: bool) -> int | None:
    """
    El id de la `crm.tag` "ENVÍO COMBINADO". UNA búsqueda por vuelta, y UN solo
    intento de crearla.

    `crear=False` (la limpieza) nunca la da de alta: retirar una etiqueta que
    no existe no es motivo para crearla. Y si la creación falla, se devuelve
    None y la vuelta sigue SIN etiquetar — la nota, que es lo que pidió
    Brandon, no depende de que la etiqueta exista. **Eso exige su propio
    try**: sin él la excepción subía hasta el bucle de órdenes y dejaba a la
    PRIMERA orden del grupo sin nota, sin entrega y sin historial, cada 15
    minutos y siempre a la misma.
    """
    if not estado["buscada"]:
        filas = kw("crm.tag", "search_read", [[["name", "=", TAG_NOMBRE]]],
                   {"fields": ["name"], "limit": 1}) or []
        estado["tag"] = int(filas[0]["id"]) if filas else None
        estado["buscada"] = True
    if estado["tag"] is None and crear and not estado["resuelta"]:
        estado["resuelta"] = True     # se intenta UNA vez, no una por orden
        r["planeado"].append({"modelo": "crm.tag", "metodo": "create",
                              "vals": {"name": TAG_NOMBRE}})
        if not dry_run:
            try:
                estado["tag"] = int(kw("crm.tag", "create", [{"name": TAG_NOMBRE}]))
                log.info("notas combinadas: se creó la etiqueta '%s' (id %s)",
                         TAG_NOMBRE, estado["tag"])
            except Exception as exc:  # noqa: BLE001 — la nota no depende de esto
                estado["tag"] = None
                log.warning("notas combinadas: no se pudo crear la etiqueta '%s' "
                            "(%s). Las notas se escriben igual, sin etiquetar.",
                            TAG_NOMBRE, str(exc)[:160])
    return estado["tag"]


def _ids_tags(registro: dict[str, Any]) -> list[int]:
    return [int(x) for x in (registro.get("tag_ids") or []) if isinstance(x, int)]


def _plan_orden(registro: dict[str, Any], bloque: str, tag: int | None,
                dry_run: bool) -> tuple[dict[str, Any], bool, bool]:
    """(vals, cambia, falta_tag) para ESE estado del registro. PURA.

    Se llama dos veces: una con la foto del principio (para `planeado`) y otra
    con lo que Odoo acaba de contestar, justo antes de escribir.
    """
    nota = registro.get("note") or ""
    actual = bloque_actual(nota)
    cambia = not (actual and _igual(actual, bloque))
    if tag is None:
        # No existe todavía (dry_run) o no se pudo crear (en vivo). Simulando se
        # DICE que se enlazaría —el simulacro existe para eso—; en vivo, sin id
        # no hay nada que escribir y la nota se pone igual.
        falta_tag = bool(dry_run)
    else:
        falta_tag = tag not in _ids_tags(registro)
    vals: dict[str, Any] = {}
    if cambia:
        vals["note"] = con_bloque(nota, bloque)
    if falta_tag:
        # Comando 4 = ENLAZAR. El 6 (reemplazar la lista) borraría las etiquetas
        # del equipo, que es exactamente lo que no se puede hacer.
        vals["tag_ids"] = [(4, tag if tag is not None else TAG_NOMBRE)]
    return vals, cambia, falta_tag


def _marcar(kw: Any, r: dict[str, Any], estado: dict[str, Any],
            o: dict[str, Any], grupos: list[dict[str, Any]],
            entregas: dict[int, dict[str, Any]], dry_run: bool) -> None:
    """Pone (o actualiza) el bloque, la etiqueta y la nota de la entrega."""
    oid = int(o["id"])
    bloque = texto_bloque(grupos, _sujeto_orden(o, grupos))

    tag = _tag(kw, estado, r, crear=True, dry_run=dry_run)
    vals, cambia, falta_tag = _plan_orden(o, bloque, tag, dry_run)

    if vals:
        r["planeado"].append({"modelo": "sale.order", "metodo": "write",
                              "ids": [oid], "nombre": o.get("name"), "vals": vals})
    if vals and not dry_run:
        # SE RE-LEE ANTES DE ESCRIBIR (como `fijar_guia`): entre la foto del
        # principio de la vuelta y este momento pasaron decenas de viajes a
        # Odoo, y alguien pudo escribir en los Términos. Ese valor gana.
        frescas = kw("sale.order", "read", [[oid], ["note", "tag_ids"]]) or []
        if not frescas:
            # La orden ya no contesta: no se escribe A CIEGAS sobre una foto
            # vieja, que es justo como se pierde una edición ajena.
            log.warning("notas combinadas: la orden %s no se pudo releer antes "
                        "de escribir; no se toca", o.get("name") or oid)
            return
        vals, cambia, falta_tag = _plan_orden(frescas[0], bloque, tag, dry_run)
    if vals and not dry_run:
        kw("sale.order", "write", [[oid], vals])
        # Y SE RE-LEE LO ESCRITO (regla de la casa): un write que contesta bien
        # no prueba que el dato quedó.
        leida = (kw("sale.order", "read", [[oid], ["note", "tag_ids"]]) or [{}])[0]
        ok_nota = (not cambia) or _igual(bloque_actual(leida.get("note")), bloque)
        ok_tag = (not falta_tag) or (tag in _ids_tags(leida))
        if ok_nota and ok_tag:
            r["verificadas"] += 1
        else:
            r["no_verificadas"] += 1
            log.warning("notas combinadas: la orden %s no quedó al re-leer "
                        "(nota=%s etiqueta=%s)", o.get("name"), ok_nota, ok_tag)
        if not ok_nota:
            # EL FRENO. Si el bloque no vuelve, la próxima vuelta lo ANTEPONDRÍA
            # otra vez y la nota crecería sin límite. Se para aquí y en las
            # siguientes: nada de historial, nada de entregas, nada de órdenes.
            estado["envenenado"] = True
            _detener("la nota escrita no volvió reconocible al re-leer (ni marca ni texto)",
                     o.get("name") or oid)
            return
    if cambia:
        r["notas_escritas"] += 1
    else:
        r["notas_iguales"] += 1
    if falta_tag:
        r["etiquetas"] += 1

    # EL HISTORIAL VA ANTES QUE LAS ENTREGAS, y no al final. Si una entrega que
    # Odoo no deja tocar lanzara primero, el apunte no se publicaría nunca: en
    # la vuelta siguiente `cambia` ya es False y el `if` no se cumple más. Lo
    # secundario (la entrega) no puede quedarse con lo del encargo.
    # SÓLO CUANDO ALGO CAMBIÓ: si no, cada 15 minutos publicaría el mismo
    # mensaje y el historial —que es donde se busca qué pasó— quedaría
    # ilegible en un día. `mail.mt_note` es nota interna: no le sale correo a
    # nadie de fuera.
    if cambia:
        # El historial habla con el MISMO vocabulario que la nota: llamarle
        # "envío combinado" a un surtido dividido aquí volvería a contradecir
        # al Excel y a la pestaña, sólo que en otro sitio.
        partes = []
        for g in grupos:
            titulo, verbo, _c = _frase_grupo(g)
            nombres = ", ".join(_texto_llano(h) for h in g["hermanas"])
            partes.append(f"<b>{titulo}</b> · Esta orden {verbo} "
                          f"{_html.escape(nombres)} "
                          f"(guía {_html.escape(str(g['guia']))}).")
        cuerpo = ("<p>" + " ".join(partes)
                  + " Imprimir la etiqueta UNA sola vez.</p>")
        r["planeado"].append({"modelo": "sale.order", "metodo": "message_post",
                              "ids": [oid], "nombre": o.get("name"),
                              "vals": {"body": cuerpo}})
        r["mensajes"] += 1
        _mensaje(kw, oid, cuerpo, o.get("name"), dry_run)

    # LA MISMA NOTA EN LA ENTREGA, que es el papel que tiene delante quien
    # empaca. Su `note` estaba vacía en todas, pero se usa la misma técnica de
    # marcas: si mañana alguien escribe ahí, no se le borra.
    for pid in sorted({p for g in grupos for p in g["pickings"]}):
        if estado["envenenado"]:
            return
        entrega = entregas.get(int(pid))
        if entrega is None:
            continue
        # CADA ENTREGA LLEVA SÓLO SU GRUPO. Si una orden tuviera dos entregas de
        # salida con guías distintas, darle a las dos el bloque entero haría que
        # cada albarán afirmara viajar también en la caja de la otra guía.
        suyos = [g for g in grupos if int(pid) in g["pickings"]] or grupos
        bloque_e = texto_bloque(suyos, f"Esta entrega ({entrega.get('name') or pid})")
        nota_e = entrega.get("note") or ""
        actual_e = bloque_actual(nota_e)
        if actual_e and _igual(actual_e, bloque_e):
            continue
        r["planeado"].append({"modelo": "stock.picking", "metodo": "write",
                              "ids": [int(pid)], "nombre": entrega.get("name"),
                              "vals": {"note": con_bloque(nota_e, bloque_e)}})
        if not dry_run:
            # Una entrega que Odoo no deja tocar NO cuenta como orden fallida:
            # la nota de la orden —el encargo— ya quedó escrita y verificada.
            try:
                frescas_p = kw("stock.picking", "read", [[int(pid)], ["note"]]) or []
                if not frescas_p:
                    continue
                nota_e = frescas_p[0].get("note") or ""
                if _igual(bloque_actual(nota_e), bloque_e):
                    continue
                kw("stock.picking", "write",
                   [[int(pid)], {"note": con_bloque(nota_e, bloque_e)}])
                leida_p = (kw("stock.picking", "read",
                              [[int(pid)], ["note"]]) or [{}])[0]
                if _igual(bloque_actual(leida_p.get("note")), bloque_e):
                    r["verificadas"] += 1
                else:
                    r["no_verificadas"] += 1
                    log.warning("notas combinadas: la entrega %s no quedó al re-leer",
                                entrega.get("name") or pid)
                    estado["envenenado"] = True
                    _detener("la nota de la entrega no volvió reconocible (ni marca ni texto)",
                             entrega.get("name") or pid)
                    return
            except Exception as exc:  # noqa: BLE001
                r["errores"] += 1
                log.warning("notas combinadas: la nota de %s quedó, pero su "
                            "entrega %s no se pudo escribir (%s)",
                            o.get("name") or oid, entrega.get("name") or pid,
                            str(exc)[:160])
                continue
        r["entregas_escritas"] += 1


def _texto_llano(hermana: Any) -> str:
    """El nombre de una hermana, sin html, para el cuerpo del historial."""
    if isinstance(hermana, dict):
        alm = str(hermana.get("almacen") or "")
        nombre = str(hermana.get("nombre") or "")
        return f"{nombre} ({alm})" if alm else nombre
    return str(hermana)


def _mensaje(kw: Any, oid: int, cuerpo: str, nombre: Any, dry_run: bool) -> None:
    """El apunte en el historial, con su propio try.

    VA APARTE Y NO CONTAGIA: la nota —lo que pidió Brandon— ya quedó escrita y
    verificada cuando se llega aquí. Si `message_post` fallara, dejar que la
    excepción subiera haría contar la orden como fallida cuando en realidad
    salió bien, y el resumen mentiría en la dirección peligrosa.
    """
    if dry_run:
        return
    try:
        kw("sale.order", "message_post", [[oid]],
           {"body": cuerpo, "message_type": "comment",
            "subtype_xmlid": "mail.mt_note"})
    except Exception as exc:  # noqa: BLE001
        log.warning("notas combinadas: la nota de %s quedó, pero no se pudo "
                    "publicar en su historial (%s)", nombre or oid, str(exc)[:160])


def _ya_salio(o: dict[str, Any], entregas: dict[int, dict[str, Any]]) -> bool:
    """¿La caja ya se fue? Todas sus entregas de salida conocidas, en `done`.

    Es la excepción de la regla 5. A los 21 días la hermana sale de la ventana y
    la que queda se ve sola: retirarle el aviso borraría el registro que pidió
    Brandon y, encima, publicaría "viaja sola" sobre una caja que salió hace
    semanas con las dos órdenes dentro.
    """
    suyas = [entregas[int(p)] for p in (o.get("picking_ids") or [])
             if int(p) in entregas]
    return bool(suyas) and all(e.get("state") == "done" for e in suyas)


def _limpiar(kw: Any, r: dict[str, Any], estado: dict[str, Any],
             o: dict[str, Any], entregas: dict[int, dict[str, Any]],
             dias: int, dry_run: bool) -> None:
    """Le quita el bloque y la etiqueta a una orden que ya NO está combinada.

    Pasa de verdad: cambió la guía, la hermana se canceló, o la propia orden se
    canceló después de quedar marcada. Sólo se retira LO NUESTRO — el resto de
    la nota y las demás etiquetas se quedan donde están.
    """
    oid = int(o["id"])
    cancelada = bool(o.get("_cancelada"))
    nota = o.get("note") or ""
    tiene_bloque = bloque_actual(nota) is not None
    tag = _tag(kw, estado, r, crear=False, dry_run=dry_run) if _ids_tags(o) else None
    tiene_tag = tag is not None and tag in _ids_tags(o)
    entregas_sucias = [int(p) for p in (o.get("picking_ids") or [])
                       if int(p) in entregas
                       and bloque_actual(entregas[int(p)].get("note")) is not None]
    if not (tiene_bloque or tiene_tag or entregas_sucias):
        return
    # LA CAJA YA SALIÓ: el aviso se queda (ver `_ya_salio`). Una cancelada sí se
    # limpia siempre: ahí no hay caja que respetar.
    if not cancelada and _ya_salio(o, entregas):
        return

    vals: dict[str, Any] = {}
    if tiene_bloque:
        vals["note"] = sin_bloque(nota)
    if tiene_tag:
        vals["tag_ids"] = [(3, tag)]     # 3 = desenlazar; NO borra la etiqueta
    if vals:
        r["planeado"].append({"modelo": "sale.order", "metodo": "write",
                              "ids": [oid], "nombre": o.get("name"), "vals": vals})
    if vals and not dry_run:
        # También aquí se re-lee antes: quitar el bloque de una foto vieja
        # borraría lo que alguien escribió mientras tanto.
        frescas = kw("sale.order", "read", [[oid], ["note", "tag_ids"]]) or []
        if not frescas:
            log.warning("notas combinadas: la orden %s no se pudo releer antes "
                        "de limpiar; no se toca", o.get("name") or oid)
            return
        nota = frescas[0].get("note") or ""
        tiene_bloque = bloque_actual(nota) is not None
        tiene_tag = tag is not None and tag in _ids_tags(frescas[0])
        vals = {}
        if tiene_bloque:
            vals["note"] = sin_bloque(nota)
        if tiene_tag:
            vals["tag_ids"] = [(3, tag)]
    if vals and not dry_run:
        kw("sale.order", "write", [[oid], vals])
        leida = (kw("sale.order", "read", [[oid], ["note", "tag_ids"]]) or [{}])[0]
        limpio = (bloque_actual(leida.get("note")) is None
                  and tag not in _ids_tags(leida))
        if limpio:
            r["verificadas"] += 1
        else:
            r["no_verificadas"] += 1
            log.warning("notas combinadas: no se pudo limpiar la orden %s",
                        o.get("name"))
    for pid in entregas_sucias:
        nota_e = entregas[pid].get("note") or ""
        r["planeado"].append({"modelo": "stock.picking", "metodo": "write",
                              "ids": [pid], "nombre": entregas[pid].get("name"),
                              "vals": {"note": sin_bloque(nota_e)}})
        if not dry_run:
            # Mismo molde que en `_marcar`: se escribe y SE COMPRUEBA. Un
            # albarán que se quedó diciendo que la caja va con otra orden es
            # exactamente el aviso viejo que manda a empacar mal.
            try:
                kw("stock.picking", "write", [[pid], {"note": sin_bloque(nota_e)}])
                leida_p = (kw("stock.picking", "read",
                              [[pid], ["note"]]) or [{}])[0]
                if bloque_actual(leida_p.get("note")) is None:
                    r["verificadas"] += 1
                else:
                    r["no_verificadas"] += 1
                    log.warning("notas combinadas: no se pudo limpiar la entrega %s",
                                entregas[pid].get("name") or pid)
            except Exception as exc:  # noqa: BLE001
                r["errores"] += 1
                log.warning("notas combinadas: no se pudo limpiar la entrega %s (%s)",
                            entregas[pid].get("name") or pid, str(exc)[:160])
                continue
        r["entregas_escritas"] += 1
    # NO SE AFIRMA UN HECHO DE EMBARQUE. El motivo puede ser que cambió la guía,
    # que la hermana se canceló o que quedó fuera de la ventana — decir "viaja
    # sola" sería inventarse cuál de los tres, y en el tercer caso además es
    # falso.
    if cancelada:
        cuerpo = ("<p><b>Se retiró el aviso de envío combinado.</b> Esta orden "
                  "está cancelada.</p>")
    else:
        cuerpo = ("<p><b>Se retiró el aviso de envío combinado.</b> Hoy esta "
                  "orden ya no comparte guía con ninguna otra: cambió la guía, "
                  "la otra se canceló, o quedó fuera de la ventana de "
                  f"{int(dias)} días.</p>")
    r["planeado"].append({"modelo": "sale.order", "metodo": "message_post",
                          "ids": [oid], "nombre": o.get("name"),
                          "vals": {"body": cuerpo}})
    r["mensajes"] += 1
    r["limpiadas"] += 1
    _mensaje(kw, oid, cuerpo, o.get("name"), dry_run)
