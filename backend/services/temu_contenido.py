"""
temu_contenido.py — Listing para TEMU: título, descripción y atributos con IA.

Hermano de `ml_atributos.py`, `tiktok_atributos.py` y `amazon_contenido.py`, con
el mismo contrato que ya funciona en producción: **la IA propone eligiendo de
listas cerradas, el CÓDIGO valida contra la plantilla real de la categoría, y lo
que no coincide NO se manda.** Es la lección de `TEC-1812-NEG`, que acabó en
"Máquinas de Coser" por confiar en un detector.

Lo que Temu impone y que no aparece en los otros canales
────────────────────────────────────────────────────────

1. **`temu.local.goods.v3.add` recibe los atributos como `{name, value[]}` de
   TEXTO, no como pid/vid.** Pero validamos por `vid` igual que en TikTok: es la
   única forma de garantizar que el texto que mandamos EXISTE en la lista
   cerrada de esa hoja. Se valida por id y se manda por nombre.

2. **LA CASCADA — esto es lo que rompe publicaciones.** Los atributos con
   `showType=1` son CONDICIONALES: no se piden siempre, se activan cuando el
   atributo padre toma cierto valor. Si se activan y van vacíos, Temu los
   autocompleta por su cuenta y **manda el producto a BORRADOR** en vez de
   publicarlo (ver "Sincronización automática de respaldo" en el Seller Center:
   *"las publicaciones que se completan automáticamente se guardan como
   borradores"*).

   Medido en vivo el 13-ago con la primera tanda: de 6 productos, los 2
   eléctricos cayeron en Borrador y los 4 no-eléctricos no.

   | SKU | valor elegido en el padre | qué activó | ¿se mandó? |
   |---|---|---|---|
   | HERR-0374-MUL | "Batería/alimentación de doble uso" | Voltaje, Tipo de clavija | no → Borrador |
   | ILUM-0089-PLA | "Enchufe para carga" | Tipo de clavija, Voltaje, Rango de tensión… | no → Borrador |
   | COM-0081-ROS | "Silicona" | (nada: el hijo pedía acero inoxidable) | — → OK |

   El manual proponía esquivarlo contestando siempre "sin electricidad" / "sin
   batería". **Eso NO se hace aquí**: un termómetro de baterías sí usa baterías,
   y declarar lo contrario mete un dato falso al catálogo que después nadie sabe
   que era mentira. La salida correcta es LLENAR los condicionales que se
   activen, y para eso existe `activados()`.

3. **La cascada es recursiva.** "Capacidad de la batería (mAh)" cuelga de
   "Características de la batería", que a su vez cuelga de "Fuente de
   alimentación". Por eso `activados()` itera hasta que deja de aparecer gente
   nueva, en vez de mirar un solo nivel.

4. **Dos formas distintas de declarar la condición**, y hay que soportar las dos:
   · `templatePropertyValueParentList`: `[{parentVids:[…], vids:[…]}]` — si el
     padre tomó uno de `parentVids`, el hijo se activa Y sus valores válidos se
     RESTRINGEN a `vids` (no a toda su lista).
   · `showCondition`: `[{parentRefPid, parentVids}]` — se usa con
     `controlType=0`, que es entrada NUMÉRICA (sin lista: `minValue`,
     `maxValue`, `valueUnitList`).

5. **Marca: se omite.** El único valor de la lista es "PICOOL" y los productos
   vivos de la tienda tienen `goodsTrademark` en null. No se fuerza `MARCA` como
   en los otros canales.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

log = logging.getLogger("omnicanal.temu_contenido")

# El sufijo del SKU es taxonomía interna de Kubera y no cambia por canal: se
# reusa la MISMA tabla que TikTok y ML en vez de mantener una tercera copia.
from services.tiktok_atributos import COLOR_SKU  # noqa: E402

# ── Límites ──────────────────────────────────────────────────────────────────
# 500 es el techo que declara la guía de Temu ("Character count: Within 500
# characters"). Es un TECHO, no un objetivo: los 152 productos vivos de la
# tienda promedian ~55 caracteres. Un título de 500 no lo lee nadie.
TITULO_MAX = 500
TITULO_IDEAL = (60, 120)
DESCRIPCION_MAX = 2000
BULLETS_ESPERADOS = 5
BULLET_MAX = 200

# NO VERIFICADO: el máximo real de goodsDesc y de bulletPoints. Nadie los ha
# medido contra la API; 2000 y 200 son topes conservadores nuestros, no de Temu.

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️←-⇿⬀-⯿]")
# Temu tiene su propio detector (`temu.local.goods.illegal.vocabulary.check`,
# probado y devuelve PASS/FAIL). Esta lista es el filtro barato de primera pasada;
# el caro y autoritativo es el de la API, que conviene llamar antes de publicar.
_PROMO = {
    "oferta", "ofertas", "gratis", "barato", "descuento", "promocion",
    "promoción", "garantizado", "envio gratis", "envío gratis", "100%",
    "el mas vendido", "el más vendido", "liquidacion", "liquidación",
    "rebaja", "ahorra", "mejor precio",
}
# OJO con lo que NO está en esa lista: "regalo" y "regalar" son descripción
# legítima de producto, no reclamo promocional — Temu tiene un atributo que se
# llama literalmente "Ocasión para regalar". Meterlo marcaba en rojo la mitad de
# las descripciones de joyería sin que hubiera nada que corregir (medido en la
# primera corrida de enriquecimiento, 13-ago).


def _sin_acentos(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _promocionales(texto: str) -> list[str]:
    t = _sin_acentos(texto)
    return [p for p in _PROMO if re.search(rf"\b{re.escape(_sin_acentos(p))}\b", t)]


# ═════════════════════════════════════════════════════════════════════════════
# LA CASCADA
# ═════════════════════════════════════════════════════════════════════════════
def duros(props: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Obligatorios que se piden SIEMPRE (`showType=0`). De 0 a 4 por hoja."""
    return [a for a in props if a.get("required") and a.get("showType") == 0]


def activados(props: list[dict[str, Any]],
              elegidos: dict[int, list[int]]) -> list[tuple[dict, set[int] | None]]:
    """Condicionales que se DESTRABARON con lo que ya se eligió.

    `elegidos`: {templatePid del padre: [vids elegidos]}.

    Devuelve [(atributo, vids_permitidos)] donde `vids_permitidos` es el
    subconjunto al que la cascada restringe ese hijo, o None si es entrada
    numérica (`controlType=0`, sin lista).

    Itera hasta punto fijo: un hijo activado puede activar a su vez a un nieto
    (Fuente de alimentación → Características de la batería → Capacidad en mAh).
    """
    por_tpid = {a.get("templatePid"): a for a in props}
    por_refpid: dict[Any, dict] = {}
    for a in props:
        por_refpid.setdefault(a.get("refPid"), a)

    vivos = dict(elegidos)
    salida: dict[Any, tuple[dict, set[int] | None]] = {}

    for _ in range(6):                       # 6 niveles es más de lo que existe
        nuevos = False
        for a in props:
            if a.get("showType") != 1 or not a.get("required"):
                continue
            tpid = a.get("templatePid")
            if tpid in salida:
                continue
            padre_vids = set(vivos.get(a.get("parentTemplatePid")) or [])

            # forma 1: la lista restringe además los valores del hijo
            tppl = a.get("templatePropertyValueParentList") or []
            if tppl:
                permitidos: set[int] = set()
                activo = False
                for regla in tppl:
                    if padre_vids & set(regla.get("parentVids") or []):
                        activo = True
                        permitidos |= set(regla.get("vids") or [])
                if activo:
                    salida[tpid] = (a, permitidos or None)
                    nuevos = True
                    continue

            # forma 2: showCondition, apunta al padre por refPid (numéricos)
            for cond in (a.get("showCondition") or []):
                pref = cond.get("parentRefPid")
                padre = por_refpid.get(pref)
                vids_padre = set(vivos.get(padre.get("templatePid")) or []) if padre else padre_vids
                if vids_padre & set(cond.get("parentVids") or []):
                    salida[tpid] = (a, None)
                    nuevos = True
                    break
        if not nuevos:
            break
    return list(salida.values())


def _fmt(attrs: list[tuple[dict, set[int] | None]], etiqueta: str,
         max_valores: int = 40) -> str:
    """Los atributos como los verá el modelo: con su pid y sus vids válidos."""
    if not attrs:
        return f"{etiqueta}: (ninguno)\n"
    out = f"{etiqueta}:\n"
    for a, permitidos in attrs:
        num = a.get("controlType") == 0 and not (a.get("values") or [])
        out += f"  - pid={a.get('pid')} · \"{a.get('name')}\" (elige máx. {a.get('chooseMaxNum') or 1})\n"
        if num:
            uni = ", ".join(u.get("valueUnit", "") for u in (a.get("valueUnitList") or []))
            out += "      NUMÉRICO: escribe un número"
            if a.get("minValue") is not None or a.get("maxValue") is not None:
                out += f" (rango {a.get('minValue')}–{a.get('maxValue')})"
            if uni:
                out += f"  unidades: {uni}"
            out += "\n"
            continue
        vals = [v for v in (a.get("values") or [])
                if permitidos is None or v.get("vid") in permitidos]
        muestra = vals[:max_valores]
        pares = " | ".join(f"{v.get('vid')}={v.get('value')}" for v in muestra)
        extra = f"  …y {len(vals)-len(muestra)} más" if len(vals) > len(muestra) else ""
        out += f"      valores: {pares}{extra}\n"
    return out


# ═════════════════════════════════════════════════════════════════════════════
# PROMPTS
# ═════════════════════════════════════════════════════════════════════════════
def build_prompt_contenido(*, sku: str, titulo_woo: str, descripcion_woo: str,
                           categoria_ruta: str, atributos_woo: dict[str, Any] | None = None) -> str:
    """Título, descripción y bullets pensados para cómo se compra en Temu."""
    sufijo = sku.split("-")[-1].upper() if "-" in sku else ""
    color = COLOR_SKU.get(sufijo)
    return f"""Eres un especialista en listings para TEMU México. Escribes en español de México.

Tu tarea: reescribir el título y la descripción de este producto para que se
entiendan solos y aparezcan en las búsquedas de Temu.

## EL PRODUCTO
- SKU: {sku}
- Título actual (de WooCommerce): {titulo_woo}
- Descripción actual: {(descripcion_woo or 'sin descripción')[:1200]}
- Categoría de Temu: {categoria_ruta}
- Atributos que ya tiene en Woo: {json.dumps(atributos_woo or {}, ensure_ascii=False)}
{f'- Color por el sufijo del SKU ({sufijo}): {color}' if color else ''}

## CÓMO SE COMPRA EN TEMU
El comprador llega por búsqueda y decide con la foto y el título. No hay marca
que lo respalde: el título tiene que decir QUÉ ES, PARA QUÉ SIRVE y su rasgo
distintivo (material, medida, cantidad de piezas, compatibilidad).

## REGLAS DEL TÍTULO
1. Entre {TITULO_IDEAL[0]} y {TITULO_IDEAL[1]} caracteres. El techo duro son {TITULO_MAX}, pero un título
   larguísimo no lo lee nadie.
2. Empieza por el sustantivo del producto, no por un adjetivo ni por la marca.
3. Incluye el dato que el comprador usa para filtrar: medida, capacidad, número
   de piezas, material o compatibilidad. Si el título viejo lo trae, consérvalo.
4. **Nada de PALABRAS EN MAYÚSCULA SOSTENIDA**, ni emojis, ni signos como ! $ * ~.
5. **Prohibido lo promocional**: "oferta", "gratis", "el mejor", "100%",
   "envío gratis", "descuento". Temu tiene un detector propio y tumba el listing.
6. No inventes atributos que no estén en el título, la descripción o los datos
   de Woo. Si no sabes el material, no lo pongas.
7. Sin nombre de marca ajena. Si el título viejo trae una, quítala.
8. **Cuidado con las marcas que parecen palabras comunes.** Temu las detecta y
   marca el listing por posible infracción. Usa el término genérico:
   velcro → "cierre de gancho y bucle" o "cierre ajustable" · curita → "vendaje
   adhesivo" · kleenex → "pañuelo desechable" · diurex → "cinta adhesiva" ·
   tupper/tupperware → "recipiente hermético" · maicena → "fécula de maíz" ·
   jacuzzi → "bañera de hidromasaje" · vaselina → "gelatina de petróleo" ·
   ziploc → "bolsa con cierre hermético" · post-it → "nota adhesiva" ·
   frisbee → "disco volador" · thermos → "termo". Medido el 14-ago: "velcro"
   en el título dispara "Potentially Infringing Terms"; quitándolo pasa.

## REGLAS DE LA DESCRIPCIÓN
1. Máximo {DESCRIPCION_MAX} caracteres, en párrafos cortos, texto plano (sin HTML).
2. Di qué es, para qué sirve, de qué está hecho y qué incluye la caja.
3. Nada de promesas de envío, precio, garantía ni devoluciones: eso lo pone Temu
   y ponerlo tú puede tumbar el listing.

## BULLETS
{BULLETS_ESPERADOS} frases de máximo {BULLET_MAX} caracteres, cada una un beneficio concreto y
verificable del producto. Empiezan con mayúscula. Sin emojis.

## SALIDA — SOLO este JSON, sin texto alrededor

{{
  "titulo": "<título nuevo>",
  "descripcion": "<descripción nueva>",
  "bullets": ["<bullet 1>", "…"],
  "flags": ["<qué dato te faltó y tuviste que omitir>"],
  "confianza": 0.0
}}

`confianza` es tu estimación honesta de 0 a 1. Un 0.5 sincero vale más que un
0.9 inflado: el panel usa ese número para decidir qué se revisa a mano."""


def build_prompt_atributos(*, sku: str, titulo: str, descripcion: str,
                           categoria_ruta: str, props: list[dict[str, Any]],
                           atributos_woo: dict[str, Any] | None = None,
                           elegidos: dict[int, list[int]] | None = None) -> str:
    """Prompt de atributos. Si `elegidos` viene, es la SEGUNDA vuelta: solo pide
    los condicionales que se destrabaron con lo ya elegido."""
    if elegidos:
        obligatorios = activados(props, elegidos)
        cabecera = ("SEGUNDA VUELTA. Con los valores que ya elegiste se "
                    "DESTRABARON estos atributos, que ahora son obligatorios.")
        opcionales: list[tuple[dict, set[int] | None]] = []
    else:
        obligatorios = [(a, None) for a in duros(props)]
        cabecera = "Estos atributos son obligatorios SIEMPRE en esta categoría."
        opcionales = [(a, None) for a in props
                      if not a.get("required") and a.get("showType") == 0][:12]

    sufijo = sku.split("-")[-1].upper() if "-" in sku else ""
    color = COLOR_SKU.get(sufijo)

    return f"""Eres un catalogador de producto para TEMU México.

## EL PRODUCTO
- SKU: {sku}
- Título: {titulo}
- Descripción: {(descripcion or 'sin descripción')[:900]}
- Categoría de Temu: {categoria_ruta}
- Atributos que ya tiene en Woo: {json.dumps(atributos_woo or {}, ensure_ascii=False)}
{f'- Color por el sufijo del SKU ({sufijo}): {color}' if color else ''}

## {cabecera}
{_fmt(obligatorios, 'OBLIGATORIOS — llénalos TODOS')}
{_fmt(opcionales, 'OPCIONALES — llena los que puedas inferir con seguridad') if opcionales else ''}

## REGLAS DURAS

1. **Devuelve el `pid` del atributo y el `vid` del valor.** Los dos salen de las
   listas de arriba. **Está PROHIBIDO inventar un vid**: un valor inventado no
   da error, se publica mal y nadie se entera.

2. **Los NUMÉRICOS** (los que dicen `NUMÉRICO`) llevan `vid: null` y el número
   en `numero`, respetando el rango. Si no lo sabes con certeza, déjalo fuera.

3. **Di la verdad sobre la energía.** Si el producto usa pilas, dilo; si se
   enchufa, dilo. Contestar "sin electricidad" para ahorrarte preguntas mete un
   dato falso al catálogo. Eso sí: elige el valor MÁS PRECISO que aplique — por
   ejemplo, si las pilas no son recargables, "Batería no recargable" es la
   respuesta correcta y además evita que se pidan datos que no existen.

4. **No fuerces.** Si un atributo no se deduce del título, la descripción o los
   datos de Woo, déjalo fuera y anótalo en `flags`. Es mejor un producto con 3
   atributos ciertos que con 8 inventados.

5. Respeta el máximo de valores que indica cada atributo.

## SALIDA — SOLO este JSON, sin texto alrededor

{{
  "atributos": [
    {{"pid": <pid>, "vid": <vid o null si es numérico>,
      "numero": "<solo para numéricos>", "razon": "<breve>"}}
  ],
  "flags": ["<pid o nombre>: por qué no se pudo determinar"],
  "confianza": 0.0
}}"""


# ═════════════════════════════════════════════════════════════════════════════
# VALIDADORES — la garantía, no el prompt
# ═════════════════════════════════════════════════════════════════════════════
def validar_contenido(contenido: dict[str, Any]) -> tuple[dict, list[str]]:
    """Devuelve (contenido, problemas). NO corrige por su cuenta: reporta."""
    problemas: list[str] = []
    c = dict(contenido or {})

    t = (c.get("titulo") or "").strip()
    if not t:
        problemas.append("titulo: vacío")
    elif len(t) > TITULO_MAX:
        problemas.append(f"titulo: {len(t)} caracteres, máximo {TITULO_MAX}")
    if _EMOJI.search(t):
        problemas.append("titulo: lleva emojis")
    for p in _promocionales(t):
        problemas.append(f"titulo: palabra promocional '{p}'")
    palabras = re.findall(r"[A-ZÁÉÍÓÚÑ]{4,}", t)
    if len(palabras) >= 3:
        problemas.append(f"titulo: {len(palabras)} palabras en mayúscula sostenida")

    d = (c.get("descripcion") or "").strip()
    if len(d) > DESCRIPCION_MAX:
        problemas.append(f"descripcion: {len(d)} caracteres, máximo {DESCRIPCION_MAX}")
    if "<" in d and ">" in d:
        problemas.append("descripcion: parece traer HTML (Temu la quiere en texto plano)")
    for p in _promocionales(d):
        problemas.append(f"descripcion: palabra promocional '{p}'")

    bullets = c.get("bullets") or []
    if isinstance(bullets, str):
        bullets = [b for b in bullets.split("\n") if b.strip()]
        c["bullets"] = bullets
    for i, b in enumerate(bullets, 1):
        if len((b or "").strip()) > BULLET_MAX:
            problemas.append(f"bullet {i}: {len(b)} caracteres, máximo {BULLET_MAX}")
        if _EMOJI.search(b or ""):
            problemas.append(f"bullet {i}: lleva emojis")
    return c, problemas


def validar_atributos(propuesta: dict[str, Any], props: list[dict[str, Any]],
                      permitidos_por_tpid: dict[Any, set[int] | None] | None = None
                      ) -> tuple[list[dict], dict[int, list[int]], list[str]]:
    """Filtra lo propuesto contra la plantilla REAL de la hoja.

    Devuelve:
      · `atributos` en el formato que quiere `temu.local.goods.v3.add`:
        `[{"name": ..., "value": [...]}]` — texto, no ids
      · `elegidos` {templatePid: [vids]}, que es lo que `activados()` necesita
        para calcular la siguiente vuelta de la cascada
      · `rechazos`, para la bitácora

    ESTA FUNCIÓN ES LA GARANTÍA. El modelo puede alucinar un vid por más
    instrucciones que lleve el prompt; aquí se comprueba contra la lista cerrada
    y lo que no cuadra NO se publica.
    """
    por_pid = {a.get("pid"): a for a in props}
    permitidos_por_tpid = permitidos_por_tpid or {}
    validos: list[dict] = []
    elegidos: dict[int, list[int]] = {}
    rechazos: list[str] = []

    for prop in (propuesta.get("atributos") or []):
        a = por_pid.get(prop.get("pid"))
        if not a:
            rechazos.append(f"pid {prop.get('pid')}: no existe en esta categoría")
            continue
        nombre = a.get("name")
        tpid = a.get("templatePid")

        # numérico: sin lista cerrada, se valida el rango
        if a.get("controlType") == 0 and not (a.get("values") or []):
            num = str(prop.get("numero") or "").strip()
            if not num:
                rechazos.append(f"{nombre}: numérico sin valor")
                continue
            try:
                v = float(num)
            except ValueError:
                rechazos.append(f"{nombre}: '{num}' no es un número")
                continue
            mn, mx = a.get("minValue"), a.get("maxValue")
            if (mn is not None and v < float(mn)) or (mx is not None and v > float(mx)):
                rechazos.append(f"{nombre}: {v} fuera del rango [{mn}, {mx}]")
                continue
            validos.append({"name": nombre, "value": [num]})
            continue

        vid = prop.get("vid")
        val = next((v for v in (a.get("values") or []) if v.get("vid") == vid), None)
        if not val:
            rechazos.append(f"{nombre}: vid {vid} inventado")
            continue
        # si la cascada restringió los valores de este hijo, se respeta
        permitidos = permitidos_por_tpid.get(tpid)
        if permitidos and vid not in permitidos:
            rechazos.append(
                f"{nombre}: '{val.get('value')}' no aplica con el valor elegido en su atributo padre")
            continue
        validos.append({"name": nombre, "value": [val.get("value")]})
        elegidos.setdefault(tpid, []).append(vid)

    return validos, elegidos, rechazos


def faltantes(props: list[dict[str, Any]], elegidos: dict[int, list[int]]) -> list[str]:
    """Obligatorios (duros + condicionales activados) que quedaron SIN llenar.

    Es el chequeo que evita el Borrador: si esto no viene vacío, publicar deja
    que Temu autocomplete y mande el producto a borrador.
    """
    pend = []
    for a in duros(props):
        if a.get("templatePid") not in elegidos:
            pend.append(a.get("name"))
    for a, _ in activados(props, elegidos):
        if a.get("templatePid") not in elegidos:
            pend.append(a.get("name"))
    return pend
