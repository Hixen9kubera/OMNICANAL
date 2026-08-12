"""
packing_comparador.py — Compara el costo que sale de un packing list contra el
costo que ya está capturado en ``costos_validados``.

Es el corazón del "Resolver" de la pantalla de Costos. El flujo es:

  1. Del nombre del archivo sale el código de contenedor (``MRKU4831449``).
  2. En ``costos_validados`` los contenedores se guardan con un sufijo de
     embarque (``MRKU4831449 - 88``), así que el match es por PREFIJO. Esto no
     es un detalle: el match exacto devuelve cero filas siempre.
  3. Ese contenedor acota los candidatos a un puñado de SKUs (10, no 15,428).
     Con un conjunto tan chico, empatar cada renglón del Excel con su SKU es un
     problema que el LLM resuelve bien y que una persona revisa de un vistazo.
  4. Se compara costo actual vs costo nuevo y se marca lo que no cuadra.

Nada de esto se persiste: es una herramienta de un solo uso. Lo único que llega
a escribir es el UPSERT final a ``costos_validados``, y solo con lo que el
usuario confirme.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx
import requests

from config import settings
from services import db

log = logging.getLogger("omnicanal.packing.comparador")

# Diferencia a partir de la cual un SKU se marca para revisión. Por debajo, la
# variación se explica por el tipo de cambio o el flete del embarque; por encima,
# casi siempre es un dato mal capturado.
UMBRAL_ALERTA = 0.30


# ── Contenedor ───────────────────────────────────────────────────────────────
def normalizar_contenedor(codigo: str) -> str:
    """
    ``'MRKU4831449 - 88'`` → ``'MRKU4831449'``.

    El sufijo ``- NN`` es el número de embarque interno; el packing list solo
    trae el código del contenedor.
    """
    return re.sub(r"\s*-\s*\d+\s*$", "", (codigo or "").strip()).strip()


def buscar_contenedor(codigo: str) -> list[dict[str, Any]]:
    """
    Contenedores de ``costos_validados`` que empiezan con ese código.

    Devuelve ``[{contenedor, n}]`` — normalmente uno, pero se devuelven todos
    porque un mismo contenedor puede aparecer con dos sufijos si se capturó en
    dos tandas, y en ese caso hay que enseñárselos al usuario en vez de elegir
    por él.
    """
    base = normalizar_contenedor(codigo)
    if not base:
        return []
    try:
        return db.fetch_all(
            "SELECT contenedor, COUNT(*) n FROM costos_validados "
            "WHERE contenedor LIKE %s GROUP BY contenedor ORDER BY n DESC",
            (f"{base}%",),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo buscar el contenedor %s: %s", base, exc)
        return []


# El sufijo opcional es la variante que el app viejo le pegaba al provisional:
# `0759-0057-PURPLE`, `2791-0015-L`, `1330-0083-WHITE-A+A`. Sin él se colaban 16
# filas — VERIFICADO: ninguna de esas existe en WooCommerce, igual que los
# provisionales pelones. Un SKU de Kubera siempre empieza con letras
# (`BAÑ-0486-EST`, `ROP-AZL-GRICLA-GRIOBS-S`), así que el ancla `^\d` no lo toca.
_RE_PROVISIONAL = re.compile(r"^\d{3,5}-\d{3,5}(?:-.+)?$")


def es_provisional(sku: str) -> bool:
    """
    ``True`` si el "SKU" es en realidad un identificador provisional del app
    viejo (``5279-0001`` = últimos 4 del contenedor + consecutivo), no un SKU de
    Kubera (``SUBCAT-####-ATRIBUTO``).

    MEDIDO en producción: de las 15,429 filas de ``costos_validados``, **6,237
    son provisionales**, y es todo-o-nada por contenedor — los cuatro más
    grandes (MRKU3085279, FFAU4457148, MRKU2054020, EITU9309801 = 5,213 filas)
    están 100% provisionales.

    Importa porque un provisional no existe en WooCommerce: no tiene nombre ni
    foto, así que el empate por texto y por imagen se quedan sin insumo. Y si
    aun así se empatara, ``guardar()`` escribiría el costo sobre el
    identificador provisional en vez del SKU real, en silencio.
    """
    return bool(_RE_PROVISIONAL.match((sku or "").strip()))


def candidatos(contenedor: str) -> list[dict[str, Any]]:
    """SKUs ya costeados de un contenedor, con todo lo que hace falta comparar.

    Cada fila lleva ``provisional``: quien empata debe excluirlos (ver
    :func:`es_provisional`).
    """
    if not contenedor:
        return []
    try:
        filas = db.fetch_all(
            """SELECT sku, contenedor, costo_producto, costo_cbm, costo_total,
                      largo, ancho, alto, peso, cajas, piezas_por_caja
               FROM costos_validados WHERE contenedor = %s ORDER BY sku""",
            (contenedor,),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudieron leer los candidatos de %s: %s", contenedor, exc)
        return []
    for f in filas:
        f["provisional"] = es_provisional(f.get("sku") or "")
    return filas


def nombres_de_skus(skus: list[str]) -> dict[str, str]:
    """
    ``{sku: nombre}`` desde WooCommerce, para que el LLM empate por descripción.

    Las tablas ``wp_*`` viven en OTRA base (la de WordPress), no en la de
    ``services/db.py``: hay que entrar por ``wp_db``. Consultarlas con la
    conexión equivocada da "Table doesn't exist", no una lista vacía.
    """
    skus = [s for s in skus if s]
    if not skus:
        return {}
    try:
        from services import wp_db

        if not wp_db.disponible():
            log.info("WordPress no configurada: el empate irá solo con dimensiones.")
            return {}
        p = wp_db._prefix()
        marcas = ",".join(["%s"] * len(skus))
        filas = wp_db._fetch_all(
            f"""SELECT pm.meta_value sku, p.post_title nombre
                FROM {p}postmeta pm
                JOIN {p}posts p ON p.ID = pm.post_id
                WHERE pm.meta_key = '_sku' AND pm.meta_value IN ({marcas})""",
            tuple(skus),
        )
        return {f["sku"]: f["nombre"] for f in filas if f.get("sku")}
    except Exception as exc:  # noqa: BLE001
        # No bloquea: sin nombres el empate usa dimensiones y cantidades, que
        # de hecho son la señal más dura. Pero se dice, no se traga.
        log.warning("Sin nombres de Woo para el empate (%s): se usarán solo "
                    "dimensiones y cantidades.", exc)
        return {}


def imagenes_de_skus(skus: list[str]) -> dict[str, str]:
    """
    ``{sku: url_de_la_foto}`` desde WooCommerce, en TRES consultas.

    ``wp_db.imagenes()`` resuelve un producto a la vez; para 139 candidatos eso
    serían 139 idas a la base. Aquí se hace por lotes: SKU → post → miniatura →
    URL. Sirve para dos cosas: enseñar la foto del catálogo junto a la del
    packing list al empatar a mano, y darle ambas al modelo de visión.
    """
    skus = [s for s in skus if s]
    if not skus:
        return {}
    try:
        from services import wp_db

        if not wp_db.disponible():
            return {}
        p = wp_db._prefix()
        marcas = ",".join(["%s"] * len(skus))

        # 1) SKU → post_id
        filas = wp_db._fetch_all(
            f"SELECT meta_value sku, post_id FROM {p}postmeta "
            f"WHERE meta_key='_sku' AND meta_value IN ({marcas})",
            tuple(skus),
        )
        por_sku = {f["sku"]: f["post_id"] for f in filas if f.get("sku")}
        if not por_sku:
            return {}

        # 2) post_id → id de la miniatura
        ids = list(por_sku.values())
        marcas_ids = ",".join(["%s"] * len(ids))
        thumbs = wp_db._fetch_all(
            f"SELECT post_id, meta_value thumb FROM {p}postmeta "
            f"WHERE meta_key='_thumbnail_id' AND post_id IN ({marcas_ids})",
            tuple(ids),
        )
        thumb_por_post = {t["post_id"]: int(t["thumb"]) for t in thumbs
                          if str(t.get("thumb") or "").isdigit()}
        if not thumb_por_post:
            return {}

        # 3) id de la miniatura → URL
        att = list(set(thumb_por_post.values()))
        marcas_att = ",".join(["%s"] * len(att))
        urls = wp_db._fetch_all(
            f"SELECT ID, guid FROM {p}posts WHERE ID IN ({marcas_att}) "
            f"AND post_type='attachment'",
            tuple(att),
        )
        url_por_id = {u["ID"]: u["guid"] for u in urls if u.get("guid")}

        return {
            sku: url_por_id[thumb_por_post[post]]
            for sku, post in por_sku.items()
            if post in thumb_por_post and thumb_por_post[post] in url_por_id
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("Sin fotos del catálogo (%s): el empate irá sin imagen.", exc)
        return {}


def buscar_sku(termino: str, limite: int = 12) -> list[dict[str, Any]]:
    """
    Busca un SKU en TODO el catálogo, no solo en los candidatos del contenedor.

    Hace falta porque el empate por contenedor tiene un punto ciego: si el SKU
    correcto quedó capturado con otro contenedor (o sin ninguno), no aparece
    entre los candidatos y el renglón se queda huérfano sin remedio. Con esto se
    puede traer a mano.

    Devuelve nombre, foto y —lo importante— el contenedor con el que está
    capturado hoy, para que se vea si se está robando un SKU de otro embarque.
    """
    q = (termino or "").strip()
    if len(q) < 2:
        return []
    try:
        from services import wp_db

        salida: dict[str, dict[str, Any]] = {}
        if wp_db.disponible():
            p = wp_db._prefix()
            filas = wp_db._fetch_all(
                f"""SELECT pm.meta_value sku, po.post_title nombre
                    FROM {p}postmeta pm
                    JOIN {p}posts po ON po.ID = pm.post_id
                    WHERE pm.meta_key='_sku'
                      AND (pm.meta_value LIKE %s OR po.post_title LIKE %s)
                      AND po.post_status <> 'trash'
                    LIMIT %s""",
                (f"%{q}%", f"%{q}%", limite),
            )
            for f in filas:
                if f.get("sku"):
                    salida[f["sku"]] = {"sku": f["sku"], "nombre": f.get("nombre") or ""}

        # Con qué contenedor está capturado hoy (o si no tiene costo todavía).
        if salida:
            marcas = ",".join(["%s"] * len(salida))
            for r in db.fetch_all(
                f"SELECT sku, contenedor, costo_total FROM costos_validados "
                f"WHERE sku IN ({marcas})",
                tuple(salida),
            ):
                if r["sku"] in salida:
                    salida[r["sku"]].update({
                        "contenedor": r.get("contenedor"),
                        "costo_total": float(r.get("costo_total") or 0),
                    })

        imgs = imagenes_de_skus(list(salida))
        for sku, d in salida.items():
            d["imagen"] = imgs.get(sku)
        return sorted(salida.values(), key=lambda d: d["sku"])
    except Exception as exc:  # noqa: BLE001
        log.warning("Búsqueda de SKU '%s' falló: %s", q, exc)
        return []


# ── Empate renglón ↔ SKU existente ───────────────────────────────────────────
def _f(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_json(texto: str) -> Any:
    t = re.sub(r"^```(?:json)?|```$", "", (texto or "").strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(t)
    except Exception:  # noqa: BLE001
        for patron in (r"\[.*\]", r"\{.*\}"):
            if m := re.search(patron, t, re.DOTALL):
                try:
                    return json.loads(m.group(0))
                except Exception:  # noqa: BLE001
                    pass
    return None


_PROMPT_EMPATE = """Empata cada renglón de un packing list con el SKU que le corresponde.

RENGLONES DEL PACKING LIST:
{filas}

SKUs YA COSTEADOS DE ESTE CONTENEDOR (los únicos candidatos posibles):
{candidatos}

Devuelve SOLO un arreglo JSON, un objeto por renglón:
[{{"fila": <n>, "sku": "<SKU candidato o null>", "confianza": "alta"|"media"|"baja",
   "razon": "<una frase corta>"}}]

Reglas:
- El SKU DEBE salir de la lista de candidatos. Si ninguno corresponde, sku = null.
- **Un mismo SKU SÍ puede repetirse en varios renglones.** El proveedor parte un
  producto en varias líneas (distinto lote, cartones de medidas ligeramente
  distintas), y esas líneas se consolidan después sumando sus cajas. Si dos
  renglones son el mismo producto, asígnales el mismo SKU a los dos — no dejes
  uno en null por no repetir.
- **La DESCRIPCIÓN es tu mejor señal.** "Auriculares" y "Audífonos inalámbricos"
  son el mismo producto aunque no digan lo mismo; tradúcelo mentalmente y busca
  el producto, no la frase.
- Las dimensiones son una señal DÉBIL y hay que usarlas con desconfianza: en el
  catálogo muchos SKUs tienen mal capturadas las medidas (traen las de la caja
  en vez de las de la pieza, o pesos de 28 kg en productos de bolsillo). Si la
  descripción encaja bien, NO descartes el empate porque las medidas no cuadren
  — dilo en la razón y déjalo con confianza media.
- Un renglón sin candidato claro es un PRODUCTO NUEVO de este embarque. Es un
  resultado válido y esperado: no fuerces un empate."""


async def _llm(prompt: str, max_tokens: int = 2000) -> str | None:
    """DeepSeek por HTTP crudo, igual que el resto del proyecto."""
    if not settings.deepseek_api_key:
        log.warning("DEEPSEEK_API_KEY no configurada.")
        return None
    try:
        async with httpx.AsyncClient() as cli:
            r = await cli.post(
                f"{settings.deepseek_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                json={
                    "model": settings.deepseek_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": max_tokens,
                },
                timeout=120.0,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM falló: %s", exc)
        return None


def _resumen_fila(i: int, f: dict[str, Any]) -> str:
    """
    Un renglón, con las dimensiones de PIEZA al frente.

    Antes se mandaban las de la CAJA mientras los candidatos traían las de
    pieza, y el modelo comparaba 42×25×14 cm (un cartón) contra unos audífonos
    y concluía "no hay candidato con esas dimensiones". Comparar caja contra
    pieza no falla ruidosamente: descarta empates buenos en silencio.
    """
    pieza = (f"pieza {_f(f.get('largo_pieza')):.1f}×{_f(f.get('ancho_pieza')):.1f}"
             f"×{_f(f.get('alto_pieza')):.1f} cm"
             if _f(f.get("largo_pieza")) else "pieza sin dimensiones")
    return (
        f"{i}. {(f.get('nombre') or f.get('producto') or '')[:45]} | "
        f"chino: {(f.get('producto_chn') or '')[:25]} | "
        f"{pieza} | peso {_f(f.get('peso_unidad')):.3f} kg | "
        f"{_f(f.get('numero_cajas')):.0f} cajas × {_f(f.get('unidades_por_caja')):.0f} pz "
        f"= {_f(f.get('unidades_totales')):.0f} | USD {_f(f.get('costo_usd')):.2f}"
    )


def _resumen_candidato(c: dict[str, Any], nombres: dict[str, str]) -> str:
    return (
        f"- {c['sku']} | {nombres.get(c['sku'], '')[:45]} | "
        f"{_f(c.get('cajas')):.0f} cajas × {_f(c.get('piezas_por_caja')):.0f} pz | "
        f"pieza {_f(c.get('largo')):.1f}×{_f(c.get('ancho')):.1f}×{_f(c.get('alto')):.1f} cm | "
        f"peso {_f(c.get('peso')):.2f} kg | costo actual ${_f(c.get('costo_total')):.2f}"
    )


# Renglones por llamada. Un contenedor real trae 1000+ filas y 139 candidatos:
# todo en un solo prompt revienta el límite de tokens y, mucho antes de eso, la
# calidad del empate se cae. En tandas el modelo tiene el problema a la vista.
_TANDA_EMPATE = 35


async def empatar(
    filas: list[dict[str, Any]], cands: list[dict[str, Any]],
    progreso: Any = None,
) -> list[dict[str, Any]]:
    """
    Propone un SKU existente para cada renglón. Devuelve una lista paralela a
    ``filas`` con ``{sku, confianza, razon}``.

    Se procesa en tandas y de forma SECUENCIAL, no en paralelo: cada tanda tiene
    que saber qué SKUs ya reclamaron las anteriores. Si corrieran a la vez, dos
    renglones distintos podrían quedarse con el mismo SKU y al guardar se
    escribiría el costo de un producto encima de otro.

    Sin candidatos (contenedor nuevo) devuelve todo en null sin gastar una
    llamada al LLM: todos los renglones son productos nuevos y eso ya se sabe.
    """
    vacio = [{"sku": None, "confianza": "baja", "razon": ""} for _ in filas]
    if not filas or not cands:
        return vacio

    nombres = nombres_de_skus([c["sku"] for c in cands])
    validos = {c["sku"] for c in cands}
    usados: set[str] = set()
    salida = list(vacio)

    for inicio in range(0, len(filas), _TANDA_EMPATE):
        tanda = filas[inicio:inicio + _TANDA_EMPATE]
        # Solo se ofrecen los candidatos que siguen libres: reduce el prompt
        # conforme avanza y le quita al modelo la tentación de repetir.
        libres = [c for c in cands if c["sku"] not in usados]
        if not libres:
            break
        if progreso:
            progreso("empatando", min(inicio + len(tanda), len(filas)), len(filas))

        prompt = _PROMPT_EMPATE.format(
            filas="\n".join(_resumen_fila(i, f) for i, f in enumerate(tanda)),
            candidatos="\n".join(_resumen_candidato(c, nombres) for c in libres),
        )
        datos = _parse_json(await _llm(prompt) or "")
        if not isinstance(datos, list):
            log.warning("Empate: tanda desde %d no devolvió una lista.", inicio)
            continue

        for item in datos:
            if not isinstance(item, dict):
                continue
            try:
                i = int(item.get("fila"))
            except (TypeError, ValueError):
                continue
            if not (0 <= i < len(tanda)):
                continue
            sku = item.get("sku")
            # Única defensa: que el SKU exista de verdad. Ya NO se bloquea el
            # repetido — el proveedor parte un producto en varias líneas y esos
            # renglones se consolidan al guardar (comp.guardar suma las cajas).
            # Bloquearlo mandaba a "sin empate" renglones que el modelo había
            # identificado bien: "coincide con X, aunque el SKU ya fue usado en
            # la fila N" era su forma de decir que sí era el mismo producto.
            if sku not in validos:
                sku = None
            if sku:
                usados.add(sku)
            salida[inicio + i] = {
                "sku": sku,
                "confianza": (item.get("confianza") or "baja").lower(),
                "razon": (item.get("razon") or "")[:200],
            }
    return salida


# ── Segunda pasada: empate por IMAGEN ────────────────────────────────────────
# El texto falla cuando el proveedor escribe "Auriculares" y el catálogo dice
# "Audífonos Invisibles Bluetooth": misma cosa, cero palabras en común. La foto
# lo resuelve de un vistazo. Se usa Haiku 4.5 porque es lo más barato con visión
# y la tarea —"¿es este producto o no?"— no necesita más.
MODELO_VISION = "claude-haiku-4-5"
_LADO_VISION = 384      # px; suficiente para reconocer un producto
_MAX_CANDIDATOS_FOTO = 12   # cuántas fotos de catálogo caben en un prompt


def _bajar_imagen(url: str) -> bytes | None:
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        return r.content
    except Exception as exc:  # noqa: BLE001
        log.debug("No se pudo bajar %s: %s", url, exc)
        return None


def _preparar(datos: bytes | None, lado: int = _LADO_VISION) -> tuple[str, str] | None:
    """``(base64, mime)`` reducido. Sin Pillow se manda tal cual si no pesa mucho."""
    if not datos:
        return None
    crudo, mime = datos, "image/jpeg"
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(datos)).convert("RGB")
        im.thumbnail((lado, lado))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=80)
        crudo = buf.getvalue()
    except Exception:  # noqa: BLE001
        if len(datos) > 400_000:
            return None
    return base64.b64encode(crudo).decode(), mime


_PROMPT_VISION = """Te doy la foto de un producto tomada de un packing list, y despues
las fotos de {n} productos del catálogo, numeradas.

Producto del packing list: {desc}

Catálogo:
{lista}

¿Cuál de los productos del catálogo es EL MISMO producto que el del packing list?

Responde SOLO este JSON:
{{"indice": <número del catálogo, o null si ninguno es el mismo>,
  "titulo_concuerda": true|false,
  "confianza": "alta"|"media"|"baja",
  "razon": "<una frase corta: qué viste que lo confirma o lo descarta>"}}

Fíjate en la forma, el tipo de producto y los detalles visibles. El color puede
variar entre la foto del proveedor y la del catálogo (son lotes distintos): si
todo lo demás coincide, sigue siendo el mismo producto. Si ninguno es el mismo,
indice = null — es un resultado válido y esperado.

**titulo_concuerda** es una pregunta APARTE de la imagen, y es la que atrapa tus
errores: ¿los dos nombres describen la misma clase de producto, aunque estén
escritos distinto? "Auriculares" y "Audífonos Inalámbricos" → true (misma cosa,
otras palabras). "Malla para sombra" y "Estante metálico" → false (una rejilla y
un estante se PARECEN en foto, pero no son el mismo producto). Contéstala mirando
solo los nombres, sin dejar que la imagen te convenza."""


async def empatar_por_imagen(
    pendientes: list[tuple[int, dict[str, Any]]],
    libres: list[dict[str, Any]],
    fotos_packing: dict[int, bytes],
    nombres: dict[str, str],
    progreso: Any = None,
) -> dict[int, dict[str, Any]]:
    """
    Segunda pasada sobre los renglones que el texto dejó sin empatar.

    ``pendientes`` son ``(indice_de_fila, fila)``; ``libres`` los candidatos que
    ningún renglón reclamó todavía. Devuelve ``{indice: {sku, confianza, razon}}``
    solo para los que resolvió.

    Es secuencial por la misma razón que el empate por texto: cada acierto quita
    un candidato del pool y no puede haber dos renglones peleándose un SKU.
    """
    if not pendientes or not libres or not settings.anthropic_api_key:
        if pendientes and libres and not settings.anthropic_api_key:
            log.info("Sin ANTHROPIC_API_KEY: se omite el empate por imagen.")
        return {}

    urls = imagenes_de_skus([c["sku"] for c in libres])
    if not urls:
        log.info("Ningún candidato tiene foto en Woo: se omite el empate por imagen.")
        return {}

    # Las fotos del catálogo se bajan UNA vez y se reutilizan en cada renglón.
    cache: dict[str, tuple[str, str]] = {}
    for sku, url in urls.items():
        if prep := _preparar(_bajar_imagen(url)):
            cache[sku] = prep
    if not cache:
        return {}

    import anthropic

    cli = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    resueltos: dict[int, dict[str, Any]] = {}
    usados: set[str] = set()

    for n, (idx, fila) in enumerate(pendientes, start=1):
        if progreso:
            progreso("empatando_fotos", n, len(pendientes))

        foto = _preparar(fotos_packing.get(fila.get("fila_idx")))
        if not foto:
            continue
        disponibles = [s for s in cache if s not in usados][:_MAX_CANDIDATOS_FOTO]
        if not disponibles:
            break

        partes: list[dict[str, Any]] = [
            {"type": "image", "source": {"type": "base64", "media_type": foto[1],
                                         "data": foto[0]}},
        ]
        for s in disponibles:
            b64, mime = cache[s]
            partes.append({"type": "image",
                           "source": {"type": "base64", "media_type": mime, "data": b64}})
        partes.append({"type": "text", "text": _PROMPT_VISION.format(
            n=len(disponibles),
            desc=(fila.get("nombre") or fila.get("producto") or "")[:60],
            lista="\n".join(f"  {i}. {nombres.get(s, s)[:50]}"
                            for i, s in enumerate(disponibles)),
        )})

        try:
            r = await cli.messages.create(
                model=MODELO_VISION, max_tokens=300,
                messages=[{"role": "user", "content": partes}],
            )
            texto = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        except Exception as exc:  # noqa: BLE001
            log.warning("Visión falló en el renglón %s: %s", idx, exc)
            continue

        data = _parse_json(texto)
        if not isinstance(data, dict):
            continue
        i = data.get("indice")
        if not isinstance(i, int) or not (0 <= i < len(disponibles)):
            continue
        sku = disponibles[i]
        usados.add(sku)

        # Dos ejes independientes: la imagen y el título. La imagen sola se
        # equivoca con confianza —una malla para sombra y un estante metálico
        # son rejillas grises a 150 px— así que un empate visual cuyo NOMBRE no
        # concuerda baja a confianza "baja" y queda fuera de "Guardar aprobados".
        #
        # Deliberadamente NO se usa la diferencia de costo como señal: el costo
        # es justamente lo que esta pantalla viene a corregir, y varios de los
        # costos viejos están mal capturados. Juzgar el empate por el costo sería
        # circular — un salto grande puede significar que el dato anterior era
        # basura, no que el empate sea malo.
        dicho = (data.get("confianza") or "media").lower()
        concuerda = data.get("titulo_concuerda")
        if concuerda is False:
            confianza, nota = "baja", "el nombre NO concuerda — revisar"
        else:
            confianza, nota = dicho, "nombre concuerda" if concuerda else ""

        resueltos[idx] = {
            "sku": sku,
            "confianza": confianza,
            "razon": " · ".join(x for x in
                                ("por imagen", nota, (data.get("razon") or "")[:140]) if x),
        }

    if resueltos:
        log.info("Empate por imagen: %d de %d renglones resueltos.",
                 len(resueltos), len(pendientes))
    return resueltos


# ── Comparación ──────────────────────────────────────────────────────────────
def comparar(
    filas: list[dict[str, Any]],
    cands: list[dict[str, Any]],
    empates: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Cruza cada renglón calculado con su SKU actual.

    Devuelve ``{filas, resumen}``. Cada fila trae los valores nuevos, los
    actuales y la diferencia relativa, más un ``estado``:

      ``nuevo``    — no empató con nada: producto nuevo del embarque.
      ``igual``    — la diferencia está por debajo del umbral.
      ``revisar``  — cambió más de lo que explica el tipo de cambio.
    """
    por_sku = {c["sku"]: c for c in cands}
    salida: list[dict[str, Any]] = []
    n_nuevos = n_revisar = n_iguales = 0

    for f, e in zip(filas, empates):
        sku = e.get("sku")
        actual = por_sku.get(sku) if sku else None
        nuevo_total = _f(f.get("costo_unitario"))

        if actual is None:
            estado, diff = "nuevo", None
            n_nuevos += 1
        else:
            viejo_total = _f(actual.get("costo_total"))
            diff = ((nuevo_total - viejo_total) / viejo_total) if viejo_total > 0 else None
            if diff is not None and abs(diff) >= UMBRAL_ALERTA:
                estado = "revisar"
                n_revisar += 1
            else:
                estado = "igual"
                n_iguales += 1

        salida.append({
            "fila": f.get("fila_excel"),
            "descripcion": f.get("nombre") or f.get("producto") or "",
            "producto_chn": f.get("producto_chn") or "",
            "imagen": f.get("imagen_b64"),
            "sku": sku,
            "sku_sugerido": sku,
            "confianza": e.get("confianza"),
            "razon_empate": e.get("razon"),
            "estado": estado,
            "diferencia": round(diff, 4) if diff is not None else None,
            # Lo que dice el packing list procesado
            "nuevo": {
                "costo_producto": _f(f.get("costo_mxn")),
                "costo_cbm": _f(f.get("costo_cbm_pieza")),
                "costo_total": nuevo_total,
                "costo_usd": _f(f.get("costo_usd")),
                # Por PIEZA (lo que se guarda en costos_validados)
                "largo": _f(f.get("largo_pieza")),
                "ancho": _f(f.get("ancho_pieza")),
                "alto": _f(f.get("alto_pieza")),
                "peso": _f(f.get("peso_unidad")),
                "cbm_por_pieza": _f(f.get("cbm_por_pieza")),
                # De la CAJA (lo que trae el packing list). Se exponen para que
                # la tabla muestre el detalle completo y se puedan capturar: el
                # usuario a menudo no sabe si el archivo trae caja o pieza.
                "largo_caja": _f(f.get("largo_caja")),
                "ancho_caja": _f(f.get("ancho_caja")),
                "alto_caja": _f(f.get("alto_caja")),
                "peso_caja": _f(f.get("peso_caja")),
                "cbm_caja": _f(f.get("cbm_caja")),
                "cajas": _f(f.get("numero_cajas")),
                "piezas_por_caja": _f(f.get("unidades_por_caja")),
                "unidades": _f(f.get("unidades_totales")),
            },
            # Lo que hay hoy en costos_validados
            "actual": None if actual is None else {
                "costo_producto": _f(actual.get("costo_producto")),
                "costo_cbm": _f(actual.get("costo_cbm")),
                "costo_total": _f(actual.get("costo_total")),
                "largo": _f(actual.get("largo")),
                "ancho": _f(actual.get("ancho")),
                "alto": _f(actual.get("alto")),
                "peso": _f(actual.get("peso")),
                "cajas": _f(actual.get("cajas")),
                "piezas_por_caja": _f(actual.get("piezas_por_caja")),
            },
        })

    # SKUs del contenedor que ningún renglón reclamó: o el packing list está
    # incompleto, o son de otro embarque del mismo contenedor. Vale la pena
    # decirlo en vez de que pasen desapercibidos.
    reclamados = {s["sku"] for s in salida if s["sku"]}
    huerfanos = [c["sku"] for c in cands if c["sku"] not in reclamados]

    return {
        "filas": salida,
        "resumen": {
            "total": len(salida),
            "nuevos": n_nuevos,
            "revisar": n_revisar,
            "iguales": n_iguales,
            "candidatos": len(cands),
            "sin_empatar": huerfanos,
        },
    }


# ── Agente: explica lo que cambió ────────────────────────────────────────────
_PROMPT_ANALISIS = """Eres el analista de costos de importación de Kubera. Revisa esta
comparación entre el costo que ya estaba capturado y el que sale del packing list nuevo.

Contenedor: {contenedor}
Tipo de cambio usado: {tc}   ·   Flete del contenedor: ${flete} MXN   ·   ${por_m3}/m³

{tabla}

Escribe un análisis BREVE en español de México, en viñetas, para alguien que va a
decidir si acepta estos costos. Cubre:

1. El panorama: cuántos SKUs suben, cuántos bajan, cuántos son nuevos.
2. Los casos que HAY QUE REVISAR ANTES DE GUARDAR, con el SKU y el porqué.
   Sospecha en particular de:
   - Cambios de más de 50% sin explicación obvia.
   - Dimensiones de pieza que parecen ser las de la CAJA (una pieza de 55×42×28 cm
     casi nunca es real): eso infla el flete y el envío de Mercado Libre.
   - Peso por unidad en cero o absurdo.
   - Costo USD en cero (el archivo era un PL sin factura).
3. Una recomendación clara: guardar todo, guardar salvo los que listaste, o no guardar.

No inventes datos que no estén en la tabla. Si algo se ve raro pero no puedes
explicarlo, dilo así. Máximo 200 palabras."""


async def analizar(
    comparacion: dict[str, Any], contenedor: str, totales: dict[str, Any],
) -> str:
    """
    Análisis en prosa de la comparación. Devuelve texto vacío si no hay LLM: es
    un extra, no puede bloquear el flujo.
    """
    filas = comparacion.get("filas") or []
    if not filas:
        return ""

    lineas = []
    for f in filas:
        act = f.get("actual")
        n = f["nuevo"]
        lineas.append(
            f"{f.get('sku') or '(nuevo)'} | {f['descripcion'][:35]} | "
            f"actual ${act['costo_total'] if act else 0:.2f} → nuevo ${n['costo_total']:.2f}"
            + (f" ({f['diferencia']*100:+.0f}%)" if f.get("diferencia") is not None else "")
            + f" | pieza {n['largo']:.1f}×{n['ancho']:.1f}×{n['alto']:.1f} cm, "
            f"{n['peso']:.3f} kg, USD {n['costo_usd']:.2f}"
        )

    texto = await _llm(_PROMPT_ANALISIS.format(
        contenedor=contenedor,
        tc=totales.get("tipo_cambio"),
        flete=f"{_f(totales.get('costo_contenedor')):,.0f}",
        por_m3=f"{_f(totales.get('costo_por_m3')):,.0f}",
        tabla="\n".join(lineas[:120]),   # tope: contenedores de 1000 renglones
    ), max_tokens=900)
    return (texto or "").strip()


# ── Copy-paste ───────────────────────────────────────────────────────────────
# Esquema ESTÁNDAR de salida, en el orden exacto que usa el equipo. Todo es por
# PIEZA salvo `cajas`. El invariante que lo amarra:
#
#     L_pieza × W_pieza × H_pieza / 1e6  ==  cbm_por_pieza
#
# no es casualidad: las dimensiones de pieza se derivan repartiendo el volumen
# del cartón, así que el volumen se conserva por construcción. Si alguna vez
# dejan de cuadrar, alguien capturó una dimensión a mano sin ajustar el CBM.
_COLUMNAS_TSV = [
    "sku", "imagen", "piezas", "cajas", "cbm_por_pieza",
    "L_pieza", "W_pieza", "H_pieza", "peso",
    "precio_usd", "tipo_cambio", "precio_mxn",
    "costo_importacion", "costo_importacion_total",
    "costo_unitario", "costo_total",
]


def tsv(comparacion: dict[str, Any], tipo_cambio: float = 0) -> str:
    """
    Tabla en el formato estándar, lista para pegar en Excel o Sheets.

    Se usa TSV y no CSV a propósito: al pegar, Excel reparte las columnas solo,
    sin el diálogo de importación ni los líos de la coma decimal en español.
    """
    filas = comparacion.get("filas") or []
    salida = ["\t".join(_COLUMNAS_TSV)]
    for f in filas:
        n = f.get("nuevo") or {}
        piezas = _f(n.get("unidades"))
        imp = _f(n.get("costo_cbm"))
        unitario = _f(n.get("costo_total"))
        valores = {
            "sku": f.get("sku") or "",
            # La foto viaja como data URI en la app; en el TSV iría un bloque
            # ilegible de kilobytes, así que se deja la columna vacía —
            # presente para que el pegado calce con la plantilla.
            "imagen": "",
            "piezas": f"{piezas:.0f}",
            "cajas": f"{_f(n.get('cajas')):.0f}",
            "cbm_por_pieza": f"{_f(n.get('cbm_por_pieza')):.6f}",
            "L_pieza": f"{_f(n.get('largo')):.2f}",
            "W_pieza": f"{_f(n.get('ancho')):.2f}",
            "H_pieza": f"{_f(n.get('alto')):.2f}",
            "peso": f"{_f(n.get('peso')):.3f}",
            "precio_usd": f"{_f(n.get('costo_usd')):.2f}",
            "tipo_cambio": f"{tipo_cambio:g}" if tipo_cambio else "",
            "precio_mxn": f"{_f(n.get('costo_producto')):.2f}",
            "costo_importacion": f"{imp:.2f}",
            "costo_importacion_total": f"{imp * piezas:.2f}",
            "costo_unitario": f"{unitario:.2f}",
            "costo_total": f"{unitario * piezas:.2f}",
        }
        salida.append("\t".join(valores[c] for c in _COLUMNAS_TSV))
    return "\n".join(salida)


# ── Guardado ─────────────────────────────────────────────────────────────────
def guardar(
    filas: list[dict[str, Any]], contenedor: str,
) -> dict[str, Any]:
    """
    UPSERT en ``costos_validados`` de los renglones que traigan SKU.

    Cada fila trae ``sku`` y los valores FINALES en ``nuevo``. Son los que el
    usuario dejó en la tabla editable, no necesariamente los que salieron del
    packing list: el punto de la pantalla es poder corregir una dimensión mal
    estimada antes de que se convierta en un fee de envío equivocado.

    Se saltan los renglones sin SKU y los que no tengan dimensiones de pieza —
    un cero ahí infla el peso volumétrico en cada venta de ese SKU.
    """
    escritos, saltados, errores = 0, [], []

    # Varios renglones pueden apuntar legítimamente al mismo SKU — el proveedor
    # parte un producto en varias líneas. costos_validados tiene UNA fila por
    # SKU, así que se CONSOLIDAN antes de escribir: se suman cajas, y costo y
    # dimensiones se toman del primero (son atributos del producto, iguales en
    # todo el grupo). Descartar el duplicado perdería sus cajas; escribir los dos
    # dejaría ganar al último en silencio.
    consolidadas: dict[str, dict[str, Any]] = {}
    for f in filas:
        sku = (f.get("sku") or "").strip()
        if not sku:
            saltados.append({"fila": f.get("fila"), "motivo": "sin SKU"})
            continue
        if es_provisional(sku):
            # Candado local: los cuatro caminos que asignan SKU ya excluyen los
            # provisionales, pero el costo de que uno se cuele es un costo
            # escrito sobre una fila que no es de ningún producto real y que
            # después se propaga al precio de todos los canales. La invariante
            # vive aquí, no en la confianza de los llamadores.
            saltados.append({"sku": sku, "motivo": "identificador provisional, "
                                                  "no es un SKU de Kubera"})
            continue
        if sku in consolidadas:
            prev = consolidadas[sku]["nuevo"]
            n = f.get("nuevo") or {}
            prev["cajas"] = _f(prev.get("cajas")) + _f(n.get("cajas"))
            continue
        consolidadas[sku] = {**f, "nuevo": dict(f.get("nuevo") or {})}

    for sku, f in consolidadas.items():
        n = f["nuevo"]
        if not (_f(n.get("largo")) and _f(n.get("ancho")) and _f(n.get("alto"))):
            saltados.append({"sku": sku, "motivo": "sin dimensiones de pieza"})
            continue

        fila = {
            "sku": sku,
            "contenedor": contenedor,
            "costo_producto": round(_f(n.get("costo_producto")), 4),
            "costo_cbm": round(_f(n.get("costo_cbm")), 4),
            "costo_total": round(_f(n.get("costo_total")), 4),
            "largo": round(_f(n.get("largo")), 2),
            "ancho": round(_f(n.get("ancho")), 2),
            "alto": round(_f(n.get("alto")), 2),
            "peso": round(_f(n.get("peso")), 3) or None,
            "cajas": round(_f(n.get("cajas")), 2) or None,
            "piezas_por_caja": round(_f(n.get("piezas_por_caja")), 2) or None,
        }
        cols = ", ".join(fila)
        marcas = ", ".join(["%s"] * len(fila))
        upd = ", ".join(f"{k}=VALUES({k})" for k in fila if k != "sku")
        try:
            db.execute(
                f"INSERT INTO costos_validados ({cols}) VALUES ({marcas}) "
                f"ON DUPLICATE KEY UPDATE {upd}",
                tuple(fila.values()),
            )
            escritos += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("costos_validados %s falló: %s", sku, exc)
            errores.append({"sku": sku, "error": str(exc)[:200]})

    log.info("Resolver '%s': %d escritos, %d saltados, %d errores",
             contenedor, escritos, len(saltados), len(errores))
    return {"escritos": escritos, "saltados": saltados, "errores": errores}
