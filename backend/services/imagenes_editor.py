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

_TXT_CLAUSE = (
    "Translate EVERY piece of written text in the image (it may be in Chinese, English "
    "or any language) into natural, correct Spanish from Mexico. Render each translation in "
    "the SAME position, size, font style, color and alignment as the original text; every "
    "character must be perfectly legible — never mirrored, garbled, cut off or invented. "
    "Also remove any brand logo, watermark, blue side borders and blue bottom banner, filling "
    "those areas with the surrounding background so the result looks natural."
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
    cambiar_modelo: bool,
    person_desc: Optional[str] = None,
) -> Optional[str]:
    qf, tt, cm = bool(quitar_fondo), bool(traducir_texto), bool(cambiar_modelo)
    if not (qf or tt or cm):
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
        tasks.append(_TXT_CLAUSE)
    if cm:
        tasks.append(
            f"Replace the person ({desc}) with {replacement}, keeping exactly the same pose, "
            f"framing, outfit and expression."
        )

    preserve = ["the exact same product (shape, color, materials and details)"]
    if not qf:
        preserve.append("the same background and scene")
    if not tt:
        preserve.append("the original text and logos exactly as they are")
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
        db.execute(
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
    """
    g = await woocommerce.galeria_producto(wc_id, sku)
    parent_id = (g or {}).get("parent_id") or wc_id

    imgs = []
    for idx, e in enumerate(entradas):
        imgs.append({
            "indice": idx,
            "wc_image_id": e.get("wc_image_id"),
            "src": e.get("src"),
            "flags": {
                "quitar_fondo": bool(e.get("quitar_fondo")),
                "traducir_texto": bool(e.get("traducir_texto")),
                "cambiar_modelo": bool(e.get("cambiar_modelo")),
            },
            "estado": "pendiente",
            "paso": "En cola…",
            "error": None,
            "nueva_url": None,
            "nuevo_id": None,
        })

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


async def _run(sku: str, parent_id: int | None) -> None:
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

            prompt = _compose_prompt(f["quitar_fondo"], f["traducir_texto"], f["cambiar_modelo"], person_desc)
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

    # Un ÚNICO PUT que reemplaza todos los IDs viejos por los nuevos (evita la
    # race condition de escrituras paralelas descrita en el flujo de WooCommerce).
    if id_map and parent_id:
        job["paso_global"] = "Actualizando galería en WooCommerce…"
        _touch(sku)
        try:
            await woocommerce.reemplazar_imagenes_galeria(int(parent_id), id_map)
        except Exception as exc:  # noqa: BLE001
            log.warning("reemplazar galería %s: %s", sku, exc)

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
