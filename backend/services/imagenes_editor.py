"""
imagenes_editor.py — Editor de imágenes de producto (WooCommerce) con IA (Gemini).

On-demand desde el Studio: por imagen se reciben los flags
  quitar_fondo / traducir_texto / cambiar_modelo
y este servicio:
  1. Descarga la imagen desde su URL de WooCommerce.
  2. (cambiar_modelo) describe la persona con Gemini para reemplazarla por una latina.
  3. Compone un prompt quirúrgico según los flags activos y edita con Gemini.
  4. Sube el resultado a WordPress Media (nuevo attachment).
  5. Reemplaza los IDs viejos por los nuevos en la galería del producto en UN SOLO
     PUT (evita la race condition de escrituras paralelas), + variaciones.
     Con GALERIA_VARIANTE y un SKU variación, el paso 5 es otro: las editadas
     van a la galería PROPIA de esa variación (`imagenes_variante.reemplazar`)
     y el padre, las hermanas y commercekit_image_gallery no se tocan.
     Con GALERIA_VARIANTE y el SKU PADRE, tras el paso 5 de siempre los ids
     viejos se cambian también en `_kubera_galeria` de las hijas que los
     tengan (`imagenes_variante.reemplazar_en_hijas`).

El avance se consulta en GET /api/imagenes/{sku}/progreso (cola en memoria), con
estado POR IMAGEN (pendiente/procesando/listo/error) para el label de carga del
Studio: en qué paso está, qué imagen se procesa y si esa imagen tuvo error.

Portado de publicaciones_ready/image_editor.py (CLI, google-genai) a async/httpx.
Registra cada intento en ml_image_edit_backlog (best-effort).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any, Optional

import httpx

from config import settings
from services import woocommerce

log = logging.getLogger("omnicanal.imagenes_editor")

GEMINI_MODEL = "gemini-3-pro-image-preview"  # "Nano Banana" (igual que crear_producto)
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_MAX_CONCURRENCIA = 3

# ── Cola / progreso en memoria (una entrada por SKU) ────────────────────────────
_jobs: dict[str, dict[str, Any]] = {}


def progreso(sku: str) -> dict[str, Any] | None:
    """Estado del job de imágenes de un SKU (o None si no hay ninguno)."""
    return _jobs.get(sku)


def _touch(sku: str) -> None:
    j = _jobs.get(sku)
    if j:
        j["actualizado"] = time.time()


def _set_img(sku: str, idx: int, estado: str, paso: str, **extra: Any) -> None:
    j = _jobs.get(sku)
    if not j:
        return
    try:
        img = j["imagenes"][idx]
    except (KeyError, IndexError):
        return
    img["estado"] = estado
    img["paso"] = paso
    for k, v in extra.items():
        img[k] = v
    j["actualizado"] = time.time()


# ══════════════════════════════════════════════════════════════════════════════
# COMPOSICIÓN DE PROMPT SEGÚN FLAGS  (portado verbatim del CLI, 8 combinaciones)
# ══════════════════════════════════════════════════════════════════════════════
_PROMPT_DESCRIBE_PERSON = (
    "Look at this image carefully. If there is a visible human person, model, or someone wearing clothes:\n"
    "Respond with ONE short English phrase describing them. Use this format:\n"
    "  [gender+age_group] approximately [age], [build], wearing [clothing description]\n"
    "\n"
    "age_group options: baby, child, teen, adult, elderly\n"
    "gender options: boy, girl, man, woman\n"
    "build options: slim, average, athletic, overweight\n"
    "\n"
    "Examples:\n"
    "  'girl approximately 6 years old, slim, wearing pink dress'\n"
    "  'woman approximately 30 years old, average, wearing sportswear'\n"
    "\n"
    "If there is NO visible person, respond only with: NO_PERSON\n"
    "Respond with the single phrase or NO_PERSON only, nothing else."
)

_TRADUCIR_CLAUSE = (
    "Translate EVERY piece of written text in the image (it may be in Chinese, English "
    "or any language) into natural, correct Spanish from Mexico. Render each translation in "
    "the SAME position, size, font style, color and alignment as the original text; every "
    "character must be perfectly legible — never mirrored, garbled, cut off or invented."
)

_LOGOS_CLAUSE = (
    "Remove any brand logo, watermark, blue side borders and blue bottom banner, filling "
    "those areas with the surrounding background so the result looks natural. Do not alter "
    "any other content of the image."
)


def _replacement_for(person_desc: str) -> str:
    d = (person_desc or "").lower()
    if any(w in d for w in ("baby", "infant", "toddler")):
        return "an attractive Latin baby of the same age and gender"
    if ("teen" not in d) and (("child" in d) or ("year old" in d)):
        if "girl" in d:
            return "an attractive Latin girl of similar age"
        if "boy" in d:
            return "an attractive Latin boy of similar age"
        return "an attractive Latin child of similar age and gender"
    if "teen" in d:
        if "girl" in d:
            return "an attractive Latin teenage girl of similar age"
        if "boy" in d:
            return "an attractive Latin teenage boy of similar age"
        return "an attractive Latin teenager of similar age and gender"
    if "elderly" in d or "old man" in d or "old woman" in d:
        if any(w in d for w in ("woman", "lady")):
            return "an attractive Latin elderly woman"
        return "an attractive Latin elderly person"
    if any(w in d for w in ("woman", "girl")):
        return "an attractive Latin woman of similar age"
    if any(w in d for w in ("man", "boy")):
        return "an attractive Latin man of similar age"
    return "an attractive Latin person of the same demographic"


def _compose_prompt(
    quitar_fondo: bool,
    traducir_texto: bool,
    quitar_logos: bool,
    cambiar_modelo: bool,
    person_desc: Optional[str] = None,
) -> Optional[str]:
    qf, tt, ql, cm = (
        bool(quitar_fondo), bool(traducir_texto), bool(quitar_logos), bool(cambiar_modelo)
    )
    if not (qf or tt or ql or cm):
        return None

    desc = person_desc or "the person"
    replacement = _replacement_for(person_desc or "") if cm else ""

    tasks = []
    if qf:
        tasks.append(
            "Replace the background with a pure, seamless white background (#FFFFFF), "
            "keeping the product exactly as it is, well centered."
        )
    if tt:
        tasks.append(_TRADUCIR_CLAUSE)
    if ql:
        tasks.append(_LOGOS_CLAUSE)
    if cm:
        tasks.append(
            f"Replace the person ({desc}) with {replacement}, keeping exactly the same pose, "
            f"framing, outfit and expression."
        )

    preserve = ["the exact same product (shape, color, materials and details)"]
    if not qf:
        preserve.append("the same background and scene")
    if not tt:
        preserve.append("the original text exactly as it is (do not translate it)")
    if not ql:
        preserve.append("the original logos and watermarks exactly as they are")
    if not cm:
        preserve.append("any person unchanged")
    preserve.append("the same layout, composition, camera angle, proportions and lighting")

    body = " ".join(f"{i+1}) {t}" for i, t in enumerate(tasks))
    guard = (
        "Do NOT re-imagine or regenerate the scene. Do NOT add, remove or move objects, props, "
        "floors, tables, shadows or people beyond what is explicitly requested above. Preserve "
        + ", ".join(preserve) +
        ". Return ONLY the edited image, at the same resolution and aspect ratio as the input."
    )
    return f"Edit this product photo with surgical precision. Tasks: {body} {guard}"


# ══════════════════════════════════════════════════════════════════════════════
# GEMINI (REST async) + descarga
# ══════════════════════════════════════════════════════════════════════════════
_DL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": f"{settings.wc_url.rstrip('/')}/" if settings.wc_url else "https://chunche.shop/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


async def _descargar(url: str) -> tuple[Optional[bytes], str, Optional[str]]:
    """Descarga una imagen. Devuelve (bytes|None, mime, error)."""
    last_err = None
    for intento in range(3):
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
                r = await cli.get(url, headers=_DL_HEADERS)
            if r.status_code == 429:
                await asyncio.sleep(min(2 + intento * 2, 8))
                last_err = "429"
                continue
            r.raise_for_status()
            mime = (r.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
            if not mime.startswith("image/"):
                mime = "image/jpeg"
            return r.content, mime, None
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(1 + intento)
    return None, "image/jpeg", f"download_error: {last_err}"


async def _gemini_describe_person(img_b64: str, mime: str) -> Optional[str]:
    if not settings.gemini_api_key:
        return None
    body = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime, "data": img_b64}},
            {"text": _PROMPT_DESCRIBE_PERSON},
        ]}],
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as cli:
            r = await cli.post(
                f"{_GEMINI_BASE}/{GEMINI_MODEL}:generateContent",
                params={"key": settings.gemini_api_key}, json=body,
            )
        if r.status_code != 200:
            return None
        for cand in r.json().get("candidates", []):
            for part in (cand.get("content") or {}).get("parts", []):
                txt = part.get("text")
                if txt:
                    t = txt.strip()
                    if t.upper().startswith("NO_PERSON") or not t:
                        return None
                    return t
    except Exception as exc:  # noqa: BLE001
        log.warning("describe_person: %s", exc)
    return None


async def _gemini_edit(
    img_b64: str, mime: str, prompt: str, retries: int = 2,
) -> tuple[Optional[bytes], str, Optional[str]]:
    """Edita la imagen con Gemini. Devuelve (bytes|None, mime_salida, error)."""
    if not settings.gemini_api_key:
        return None, mime, "GEMINI_API_KEY no configurada"
    body = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": mime, "data": img_b64}},
            {"text": prompt},
        ]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    last_err = "sin imagen en la respuesta"
    for intento in range(retries):
        if intento > 0:
            await asyncio.sleep(8)
        try:
            async with httpx.AsyncClient(timeout=240.0) as cli:
                r = await cli.post(
                    f"{_GEMINI_BASE}/{GEMINI_MODEL}:generateContent",
                    params={"key": settings.gemini_api_key}, json=body,
                )
            if r.status_code != 200:
                last_err = f"Gemini HTTP {r.status_code}: {r.text[:160]}"
                log.warning(last_err)
                continue
            data = r.json()
            textos = []
            for cand in data.get("candidates", []):
                for part in (cand.get("content") or {}).get("parts", []):
                    inline = part.get("inlineData") or part.get("inline_data") or {}
                    if inline.get("data"):
                        out_mime = (inline.get("mimeType") or inline.get("mime_type") or "image/png").lower()
                        return base64.b64decode(inline["data"]), out_mime, None
                    if part.get("text"):
                        textos.append(part["text"].strip())
            last_err = "sin imagen" + (f" — {' | '.join(textos)[:200]}" if textos else "")
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            log.warning("gemini_edit: %s", last_err)
    return None, mime, last_err


# ══════════════════════════════════════════════════════════════════════════════
# BACKLOG (best-effort)
# ══════════════════════════════════════════════════════════════════════════════
def _backlog(sku: str, wc_id: int | None, item: dict, info: dict) -> None:
    try:
        from services import db
        with db.get_cursor() as cur:
            cur.execute(
                """INSERT INTO ml_image_edit_backlog
                     (run_key, cuenta, sku, wc_id, wc_image_id, src_url,
                      flag_quitar_fondo, flag_traducir_texto, flag_cambiar_modelo,
                      action, person_desc, prompt_used, gemini_model,
                      gemini_success, gemini_error, bytes_in, bytes_out,
                      wp_media_id_new, wp_url_new, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())""",
                (
                    f"studio:{sku}", "studio", sku, wc_id, item.get("wc_image_id"), item.get("src"),
                    int(item["flags"]["quitar_fondo"]), int(item["flags"]["traducir_texto"]),
                    int(item["flags"]["cambiar_modelo"]),
                    info.get("action"), info.get("person_desc"), info.get("prompt_used"), GEMINI_MODEL,
                    int(bool(info.get("gemini_success"))), info.get("gemini_error"),
                    info.get("bytes_in"), info.get("bytes_out"),
                    info.get("wp_media_id_new"), info.get("wp_url_new"),
                ),
            )
            backlog_id = cur.lastrowid
        # Espejo kubera: la edición queda como submission de imagen (resumen;
        # prompts/bytes se quedan en MySQL, viajan solo vía detail_ref).
        from services import kubera_mirror
        kubera_mirror.espejar(
            "services/imagenes_editor.py", "_backlog",
            "ml_image_edit_backlog", "ops.channel_submissions", "INSERT",
            {"canal": "mercado_libre", "cuenta": "studio", "sku": sku,
             "submission_id": str(info.get("wp_media_id_new") or "") or None,
             "operacion": "imagen", "status": info.get("action"),
             "success": bool(info.get("gemini_success")),
             "error_resumen": info.get("gemini_error"),
             "detail_ref": f"mysql:ml_image_edit_backlog:{backlog_id}" if backlog_id else None},
            clave=sku)
    except Exception as exc:  # noqa: BLE001
        log.debug("backlog imagen (ignorado): %s", exc)


# ══════════════════════════════════════════════════════════════════════════════
# ORQUESTADOR
# ══════════════════════════════════════════════════════════════════════════════
async def iniciar(sku: str, wc_id: int | None, entradas: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Crea el job y lo lanza en segundo plano. `entradas`: lista de
    {wc_image_id, src, quitar_fondo, traducir_texto, cambiar_modelo}.
    Devuelve {ok, total, parent_id}.

    Con GALERIA_VARIANTE y un SKU que es VARIACIÓN, rama propia (ver
    `_iniciar_variante`). Apagada o no-variación: idéntico a siempre.
    """
    if settings.galeria_variante:
        var = await _variante(sku, wc_id)
        if var:
            return await _iniciar_variante(sku, var, entradas)
    g = await woocommerce.galeria_producto(wc_id, sku)
    parent_id = (g or {}).get("parent_id") or wc_id

    imgs = _items(entradas)
    _jobs[sku] = {
        "sku": sku,
        "wc_id": parent_id,
        "estado": "procesando",
        "total": len(imgs),
        "procesadas": 0,
        "paso_global": "Procesando imágenes…",
        "actualizado": time.time(),
        "imagenes": imgs,
    }
    asyncio.create_task(_run(sku, parent_id))
    return {"ok": True, "total": len(imgs), "parent_id": parent_id}


def _items(entradas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Las entradas del Studio como filas del job (estado por imagen)."""
    imgs = []
    for idx, e in enumerate(entradas):
        imgs.append({
            "indice": idx,
            "wc_image_id": e.get("wc_image_id"),
            "src": e.get("src"),
            "flags": {
                "quitar_fondo": bool(e.get("quitar_fondo")),
                "traducir_texto": bool(e.get("traducir_texto")),
                "quitar_logos": bool(e.get("quitar_logos")),
                "cambiar_modelo": bool(e.get("cambiar_modelo")),
            },
            "estado": "pendiente",
            "paso": "En cola…",
            "error": None,
            "nueva_url": None,
            "nuevo_id": None,
        })
    return imgs


async def _variante(sku: str, wc_id: int | None) -> tuple[int, int] | None:
    """
    (wc_id, padre) si el SKU es una variación. Sin base de WordPress, si la
    REST dice que es variación se niega (`SinBaseWP` → 503): caer a la rama de
    siempre reemplazaría fotos en la galería del PADRE y en las hermanas.
    """
    from services import imagenes_variante
    try:
        return await asyncio.to_thread(imagenes_variante.resolver, sku, wc_id)
    except imagenes_variante.SinBaseWP:
        g = await woocommerce.galeria_producto(wc_id, sku)
        if (g or {}).get("es_variacion"):
            raise
        return None


async def _iniciar_variante(sku: str, var: tuple[int, int],
                            entradas: list[dict[str, Any]]) -> dict[str, Any]:
    """
    "Procesar con IA" para UNA variante (GALERIA_VARIANTE encendida).

    Solo acepta fotos de ESA variante: propias o heredadas del padre, por
    `wc_image_id`. Cualquier otro id (o una entrada sin id) → `GaleriaInvalida`
    (400) ANTES de gastar Gemini: en esta rama no hay "galería del padre" donde
    reemplazar, y una foto sin id no tendría a quién sustituir.

    Al terminar, `imagenes_variante.reemplazar` coloca las editadas: la de una
    propia ocupa su lugar; la de una heredada entra a la galería propia (copia
    al escribir). Padre, hermanas y `commercekit_image_gallery` NO se tocan.
    """
    from services import imagenes_variante

    wc_var, padre = var
    p = await asyncio.to_thread(imagenes_variante.propias, wc_var)
    if p is None:
        raise imagenes_variante.GaleriaInvalida(f"{sku} no es una variación.")
    h = await asyncio.to_thread(imagenes_variante.heredadas, wc_var) or []
    permitidas = set(imagenes_variante.ids_editables(p)) | {x["id"] for x in h if x.get("src")}
    ajenas = [e.get("wc_image_id") for e in entradas
              if not e.get("wc_image_id") or int(e["wc_image_id"]) not in permitidas]
    if ajenas:
        raise imagenes_variante.GaleriaInvalida(
            f"Estas fotos no son de la variante {sku} (ni propias ni heredadas del "
            f"padre): {ajenas}. Recarga la galería.")
    # El `src` del cliente se ignora: se edita el archivo del id VALIDADO. Con
    # un estado viejo tras reordenar/adoptar, la pantalla podía mandar el id de
    # la principal de TEC-0664-ROS con el src de TEC-0664-AZL.png, y la editada
    # (azul) habría ocupado el lugar de la rosa: la mezcla que esta fase quita.
    srcs = {int(i["id"]): i["src"] for i in [p.get("principal"), *(p.get("galeria") or []), *h]
            if i and i.get("id") and i.get("src")}
    sin_archivo = [int(e["wc_image_id"]) for e in entradas if int(e["wc_image_id"]) not in srcs]
    if sin_archivo:
        raise imagenes_variante.GaleriaInvalida(
            f"Estas fotos de {sku} no tienen archivo en Medios: {sin_archivo}.")
    entradas = [{**e, "src": srcs[int(e["wc_image_id"])]} for e in entradas]

    imgs = _items(entradas)
    _jobs[sku] = {
        "sku": sku,
        "wc_id": wc_var,
        "padre_wc_id": padre,
        "es_variante": True,
        "estado": "procesando",
        "total": len(imgs),
        "procesadas": 0,
        "paso_global": "Procesando imágenes…",
        "actualizado": time.time(),
        "imagenes": imgs,
    }
    asyncio.create_task(_run(sku, wc_var, variante=True))
    return {"ok": True, "total": len(imgs), "parent_id": padre,
            "wc_id": wc_var, "es_variante": True}


async def _run(sku: str, parent_id: int | None, variante: bool = False) -> None:
    """`variante=True`: `parent_id` es el wc_id de la VARIACIÓN (ver `_iniciar_variante`)."""
    job = _jobs.get(sku)
    if not job:
        return
    sem = asyncio.Semaphore(_MAX_CONCURRENCIA)
    id_map: dict[int, int] = {}

    async def _una(item: dict[str, Any]) -> None:
        idx = item["indice"]
        old_id = item["wc_image_id"]
        f = item["flags"]
        info: dict[str, Any] = {"action": "error", "gemini_success": False}
        async with sem:
            _set_img(sku, idx, "procesando", "Descargando imagen…")
            data, mime, derr = await _descargar(item["src"])
            if data is None:
                _set_img(sku, idx, "error", "Error al descargar", error=derr)
                info["gemini_error"] = derr
                _backlog(sku, parent_id, item, info)
                _bump(sku)
                return
            info["bytes_in"] = len(data)
            img_b64 = base64.b64encode(data).decode()

            person_desc = None
            if f["cambiar_modelo"]:
                _set_img(sku, idx, "procesando", "Analizando persona…")
                person_desc = await _gemini_describe_person(img_b64, mime)
                info["person_desc"] = person_desc

            prompt = _compose_prompt(
                f["quitar_fondo"], f["traducir_texto"], f.get("quitar_logos", False),
                f["cambiar_modelo"], person_desc,
            )
            info["prompt_used"] = prompt
            if not prompt:  # sin flags → nada que hacer
                _set_img(sku, idx, "sin_flags", "Sin cambios")
                _bump(sku)
                return

            _set_img(sku, idx, "procesando", "Editando con IA…")
            edited, out_mime, gerr = await _gemini_edit(img_b64, mime, prompt)
            if edited is None:
                _set_img(sku, idx, "error", "La IA no devolvió imagen", error=gerr)
                info["gemini_error"] = gerr
                _backlog(sku, parent_id, item, info)
                _bump(sku)
                return
            info["bytes_out"] = len(edited)

            _set_img(sku, idx, "procesando", "Subiendo a WooCommerce…")
            subida = await woocommerce.subir_imagen_wp(f"{sku}-edit-{idx + 1}", edited, out_mime)
            if not subida:
                _set_img(sku, idx, "error", "Error al subir a WordPress")
                info["gemini_error"] = "upload_error"
                _backlog(sku, parent_id, item, info)
                _bump(sku)
                return
            new_id, new_url = subida
            if old_id:
                id_map[int(old_id)] = int(new_id)
            info.update(action="edited", gemini_success=True, wp_media_id_new=new_id, wp_url_new=new_url)
            _set_img(sku, idx, "listo", "Listo", nueva_url=new_url, nuevo_id=new_id)
            _backlog(sku, parent_id, item, info)
            _bump(sku)

    await asyncio.gather(*[_una(i) for i in job["imagenes"]], return_exceptions=True)

    if variante:
        # Rama de variante: una escritura bajo el candado de la variación, sobre
        # la galería RELEÍDA en ese momento (la IA tardó minutos).
        if id_map and parent_id:
            from services import imagenes_variante
            job["paso_global"] = "Actualizando la galería de la variante…"
            _touch(sku)
            try:
                res = await imagenes_variante.reemplazar(int(parent_id), id_map)
                job["galeria_ok"] = bool(res.get("ok"))
                job["galeria_aviso"] = res.get("aviso")
            except Exception as exc:  # noqa: BLE001
                log.warning("galería variante %s: %s", sku, exc)
                job["galeria_ok"] = False
                job["galeria_aviso"] = f"No se pudo guardar la galería: {exc}"
    # Un ÚNICO PUT que reemplaza todos los IDs viejos por los nuevos (evita la
    # race condition de escrituras paralelas descrita en el flujo de WooCommerce).
    elif id_map and parent_id:
        job["paso_global"] = "Actualizando galería en WooCommerce…"
        _touch(sku)
        try:
            await woocommerce.reemplazar_imagenes_galeria(int(parent_id), id_map)
        except Exception as exc:  # noqa: BLE001
            log.warning("reemplazar galería %s: %s", sku, exc)
        # A2 · Con GALERIA_VARIANTE, las hijas que ya ADOPTARON una foto del
        # padre la tienen copiada en su `_kubera_galeria`, que la rama de arriba
        # no conoce: seguirían publicando la original sin editar. Se propagan
        # los ids SÓLO en las hijas que tengan alguno (cada una bajo su candado);
        # la miniatura de las hijas ya la cambió `reemplazar_imagenes_galeria` y
        # no se vuelve a escribir. Va DESPUÉS y no en paralelo: así la relectura
        # de cada hija ya ve su miniatura nueva. Flag apagado: no se llama.
        if settings.galeria_variante:
            from services import imagenes_variante
            try:
                job["hijas_galeria"] = await imagenes_variante.reemplazar_en_hijas(
                    int(parent_id), id_map)
            except Exception as exc:  # noqa: BLE001
                log.warning("galería de hijas %s: %s", sku, exc)
                job["hijas_galeria"] = {"error": str(exc)}

    errores = sum(1 for i in job["imagenes"] if i["estado"] == "error")
    job["estado"] = "completado"
    job["paso_global"] = (
        "Completado" if not errores else f"Completado con {errores} error(es)"
    )
    _touch(sku)


def _bump(sku: str) -> None:
    j = _jobs.get(sku)
    if j:
        j["procesadas"] = min(j["total"], j.get("procesadas", 0) + 1)
        j["actualizado"] = time.time()
