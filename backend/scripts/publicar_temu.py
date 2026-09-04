"""
publicar_temu.py — El publicador de Temu, en tres fases.

Portado del que corrió en vivo el 13 y 14-ago-2026 y dejó **201 productos**
publicados (351 en el catálogo). Aquí reusa `services/temu.py` (cliente async
con firma) y `services/temu_contenido.py` (prompts + validadores + cascada), en
vez de la copia de scratchpad.

    python -m scripts.publicar_temu fichas                 # 1. Woo -> JSON
    python -m scripts.publicar_temu enriquecer --tanda 1   # 2. IA, por tandas
    python -m scripts.publicar_temu publicar --aplicar     # 3. alta + bitácora

POR QUÉ TRES FASES Y NO UNA (misma receta que TikTok)
─────────────────────────────────────────────────────
El contenido con IA es lo caro: 3 llamadas a DeepSeek por producto. Si se
mezclara con el alta, un fallo de red obligaría a regenerarlo todo. Se genera
UNA vez, se guarda en JSON, y publicar lee de ahí — un producto que falla se
reintenta **sin volver a gastar IA**. Las tres fases son resumibles.

LO QUE NO SE PUEDE DESHACER
───────────────────────────
**Borrar un producto NO libera su `externalSkuId`** (`150010090 SKU
duplicated`, permanente). Por eso:
  · `bg.local.goods.out.sn.check` corre antes de cada tanda (lotes de 50, y
    **sin repetidos**: dos veces el mismo SKU en una llamada da `150010003`);
  · el payload se escribe a disco ANTES de mandarse;
  · `temu.local.goods.illegal.vocabulary.check` corre antes del alta — rechazar
    ahí NO consume el SKU; rechazar después sí.

EL PRECIO — lo que costó más medir
──────────────────────────────────
`skuList[].price.basePrice.amount` va en **DECIMAL de 2 cifras**, no en
centavos (MXN: 2 decimales, rango `[0.02, 99999999.99]`). Se ESCRIBE decimal y
se LEE en centavos en `detail.query`. Mandar centavos publica a **100×**: pasó
con `JUGU-1158-VER` ($16,402 en vez de $164.02) y `ACC-0160-AZL`, que llegó a
tener precio de anaquel de **$31,251.87**.

`basePrice` es **nuestro neto de liquidación**, no lo que paga el cliente:
Temu calcula el anaquel encima. Medido el 14-ago sobre 11 productos nuestros,
`retail = base × 1.3688` con dispersión de 0.2% → **Temu se queda con el 26.9%
del anaquel**. OJO: el factor NO es único — los productos viejos de M2E salen
en ~1.13. Hay tabla de comisiones por categoría o cohorte.

`listPrice` se OMITE a propósito: es opcional y Temu lo exige **estrictamente
mayor** que `basePrice`; mandarlo igual lo descarta en silencio. Incluirlo
obligaría a inventar un precio tachado.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import supabase_db as sdb, temu, temu_contenido as tc  # noqa: E402
from services import imagenes_amazon as ia, woocommerce as wc, wp_db  # noqa: E402
from services.ml_atributos import _deepseek_json  # noqa: E402

CANAL, CUENTA = "temu", "KUBERA"
PLANTILLA_FLETE = "LFT-18510029444014331627"   # la ÚNICA que existe; se llama "test"
DIAS_DESPACHO = 2
DATOS = pathlib.Path(__file__).parent / "_temu"
DATOS.mkdir(exist_ok=True)
FICHAS, ENRIQUECIDO = DATOS / "fichas.json", DATOS / "enriquecido.json"
APARTADOS, PAYLOADS = DATOS / "apartados.json", DATOS / "payloads.json"

# Filtro de negocio. STOCK > 0 es lo más importante: publicar sin stock es una
# venta que se cancela, y en Temu las cancelaciones pegan en la métrica de la
# tienda. El PISO de precio no es capricho: `basePrice` es lo que cobramos, y
# `CTL-0520-NEG-BLN-AC110` tiene $1.00 de precio regular en Woo siendo un
# control remoto. Un precio roto en Woo se convierte en vender a pérdida sin
# que nada dé error.
MIN_STOCK, MIN_PRECIO, MAX_PRECIO = 1, 10.0, 300.0


def _leer(p: pathlib.Path, defecto):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else defecto


def _escribir(p: pathlib.Path, d):
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════
# FASE 1 — WOO. Sin IA: extracción pura, en consultas MASIVAS (no una por SKU).
# ═════════════════════════════════════════════════════════════════════════════
METAS = ["_regular_price", "_price", "_stock", "_weight", "_length", "_width",
         "_height", "_thumbnail_id", "_product_image_gallery"]


def _bulk(sql: str, ids: list[int], extra: tuple = ()) -> list[dict]:
    out = []
    for i in range(0, len(ids), 500):
        t = ids[i:i + 500]
        ph = ",".join(["%s"] * len(t))
        out += wp_db._fetch_all(sql.format(ph=ph, P=wp_db._prefix()),
                                tuple(t) + extra)
    return out


def fase_fichas(skus: list[str]) -> dict:
    """{sku: ficha} desde Woo — precio regular, stock, peso, medidas, galería."""
    wc_map = wp_db.productos_por_sku(skus)
    ids = [v["wc_id"] for v in wc_map.values()]
    padres = [v["parent_id"] for v in wc_map.values() if v.get("parent_id")]
    todos_ids = ids + padres

    posts = {r["ID"]: r for r in _bulk(
        "SELECT ID, post_title, post_content FROM {P}posts WHERE ID IN ({ph})",
        todos_ids)}
    metas: dict[int, dict] = {}
    kh = ",".join(["%s"] * len(METAS))
    for r in _bulk("SELECT post_id, meta_key, meta_value FROM {P}postmeta "
                   "WHERE post_id IN ({ph}) AND meta_key IN (" + kh + ")",
                   todos_ids, tuple(METAS)):
        metas.setdefault(r["post_id"], {})[r["meta_key"]] = r["meta_value"]

    adj = set()
    for m in metas.values():
        if str(m.get("_thumbnail_id") or "").isdigit():
            adj.add(int(m["_thumbnail_id"]))
        for x in str(m.get("_product_image_gallery") or "").split(","):
            if x.strip().isdigit():
                adj.add(int(x.strip()))
    urls = {r["ID"]: r["guid"] for r in _bulk(
        "SELECT ID, guid FROM {P}posts WHERE ID IN ({ph}) AND post_type='attachment'",
        sorted(adj)) if r.get("guid")}

    def num(v, d=None):
        try:
            f = float(str(v).strip())
            return f if f > 0 else d
        except (TypeError, ValueError, AttributeError):
            return d

    def galeria(m):
        idl = []
        if str(m.get("_thumbnail_id") or "").isdigit():
            idl.append(int(m["_thumbnail_id"]))
        for x in str(m.get("_product_image_gallery") or "").split(","):
            if x.strip().isdigit() and int(x.strip()) not in idl:
                idl.append(int(x.strip()))
        return [urls[i] for i in idl if i in urls]

    fichas = {}
    for sku in skus:
        p = wc_map.get(sku)
        if not p:
            continue
        wid, pid = p["wc_id"], p.get("parent_id")
        m, mp = metas.get(wid) or {}, (metas.get(pid) or {} if pid else {})
        post, postp = posts.get(wid) or {}, (posts.get(pid) or {} if pid else {})

        def dato(k):
            return num(m.get(k), num(mp.get(k)))

        f = {
            "sku": sku, "wc_id": wid,
            "titulo": (post.get("post_title") or postp.get("post_title") or "").strip(),
            "descripcion": (post.get("post_content")
                            or postp.get("post_content") or "").strip()[:4000],
            "precio": dato("_regular_price") or dato("_price"),
            "stock": p.get("stock"),
            "peso": dato("_weight"), "largo": dato("_length"),
            "ancho": dato("_width"), "alto": dato("_height"),
            "imagenes": galeria(m) or (galeria(mp) if pid else []),
        }
        f["_falta"] = [k for k in ("titulo", "precio", "imagenes") if not f.get(k)]
        fichas[sku] = f
    return fichas


# ═════════════════════════════════════════════════════════════════════════════
# FASE 2 — LA IA. Tres decisiones, tres prompts, y todo validado por código.
# ═════════════════════════════════════════════════════════════════════════════
PROMPT_CAT = """Eres un catalogador de producto para TEMU Mexico.

PRODUCTO: {titulo}
DESCRIPCION: {desc}

El recomendador de Temu propuso estas categorias. Elige la que DE VERDAD
corresponde al producto.

{lista}

REGLAS
1. Fijate en QUE ES el producto, no en las palabras que aparecen en el titulo.
   Una REFACCION no va en la categoria del aparato completo: un piston de
   repuesto para silla NO va en "Sillas de oficina". Un proyector de luces NO
   va en "Series de luces".
2. Si NINGUNA corresponde, devuelve catId 0. Publicar en la categoria
   equivocada no da error: el producto queda donde nadie lo busca.

SALIDA — solo JSON:
{{"catId": <catId elegido o 0>, "razon": "<breve>"}}"""


class SinCategoria(RuntimeError):
    """Ninguna candidata encaja: el SKU se APARTA, no se publica mal. Así
    además queda LIBRE — no se quema."""


async def _ruta(cat_id: int, cache: dict) -> str:
    """`catId` -> 'Nivel1 > … > Hoja'. `cats.get` solo camina hacia ABAJO, así
    que el árbol se arma una vez y se cachea."""
    return cache.get(int(cat_id), "")


async def enriquecer_uno(f: dict, arbol: dict) -> dict:
    sku = f["sku"]
    r = await temu.llamar("bg.local.goods.category.recommend",
                          {"goodsName": f["titulo"][:120]})
    cands = list(dict.fromkeys((r.get("catIdList") or [])
                               + ([r["catId"]] if r.get("catId") else [])))
    ops = [(c, arbol.get(int(c), "")) for c in cands]
    ops = [(c, p) for c, p in ops if p]
    if not ops:
        if not cands:
            raise SinCategoria("el recomendador no devolvió categoría")
        cat, origen = int(cands[0]), "recomendador (sin árbol)"
    else:
        j = await _deepseek_json(
            "Eres un catalogador. Devuelves SOLO JSON valido.",
            PROMPT_CAT.format(titulo=f["titulo"],
                              desc=(f.get("descripcion") or "")[:600],
                              lista="\n".join(f"  catId={c}  {p}" for c, p in ops)))
        pick, razon = (j or {}).get("catId"), str((j or {}).get("razon") or "")[:90]
        if pick in (0, "0", None):
            raise SinCategoria(f"ninguna categoría encaja: {razon}")
        cat = int(pick) if pick in {c for c, _ in ops} else int(ops[0][0])
        origen = "IA" if pick == cat else "recomendador"

    props = ((await temu.llamar("bg.local.goods.template.get",
                                {"catId": str(cat), "language": "es"}))
             .get("templateInfo") or {}).get("goodsProperties") or []
    if not props:
        raise SinCategoria(f"sin plantilla para la hoja {cat}")
    ruta = arbol.get(cat, str(cat))

    cont = await _deepseek_json(
        "Eres un redactor de catalogo. Devuelves SOLO JSON valido.",
        tc.build_prompt_contenido(sku=sku, titulo_woo=f["titulo"],
                                  descripcion_woo=f.get("descripcion") or "",
                                  categoria_ruta=ruta))
    cont, probs = tc.validar_contenido(cont or {})
    titulo = (cont.get("titulo") or "").strip() or f["titulo"]
    desc = (cont.get("descripcion") or "").strip() or f["titulo"]

    attrs, elegidos, rech = [], {}, []
    if tc.duros(props):
        p1 = await _deepseek_json(
            "Eres un catalogador. Devuelves SOLO JSON valido.",
            tc.build_prompt_atributos(sku=sku, titulo=titulo, descripcion=desc,
                                      categoria_ruta=ruta, props=props))
        attrs, elegidos, rech = tc.validar_atributos(p1 or {}, props)

    # Segunda vuelta: la CASCADA. Un condicional activado y vacío hace que Temu
    # autocomplete y mande el producto a BORRADOR — no da error.
    act = tc.activados(props, elegidos)
    if act:
        p2 = await _deepseek_json(
            "Eres un catalogador. Devuelves SOLO JSON valido.",
            tc.build_prompt_atributos(sku=sku, titulo=titulo, descripcion=desc,
                                      categoria_ruta=ruta, props=props,
                                      elegidos=elegidos))
        a2, e2, r2 = tc.validar_atributos(
            p2 or {}, props, {a.get("templatePid"): v for a, v in act})
        attrs += a2
        for k, v in e2.items():
            elegidos.setdefault(k, []).extend(v)
        rech += r2

    return {
        "sku": sku, "catId": cat, "categoria_ruta": ruta, "categoria_origen": origen,
        "titulo": titulo[:tc.TITULO_MAX], "descripcion": desc[:tc.DESCRIPCION_MAX],
        "bullets": (cont.get("bullets") or [])[:tc.BULLETS_ESPERADOS],
        "atributos": attrs,
        "condicionales_activados": [a.get("name") for a, _ in act],
        "obligatorios_sin_llenar": tc.faltantes(props, elegidos),
        "confianza": cont.get("confianza"),
        "problemas_contenido": probs, "rechazos_atributos": rech,
        # lo de Woo se copia tal cual: es la fuente de verdad
        **{k: f.get(k) for k in ("precio", "stock", "peso", "largo", "ancho",
                                 "alto", "imagenes")},
        "titulo_woo": f["titulo"],
    }


# ═════════════════════════════════════════════════════════════════════════════
# FASE 3 — PUBLICAR, con bitácora obligatoria
# ═════════════════════════════════════════════════════════════════════════════
_fallos_bitacora: list[str] = []


def registrar(sku: str, ok: bool, lote: str, submission_id=None, error=None,
              status=None, operacion="create_product") -> None:
    """A `ops.channel_submissions`. NUNCA revienta el lote si Supabase falla.

    Tres reglas que ya costaron confusión en otros canales:
      1. Es bitácora de INTENTOS, no de estado: INSERT plano, sin `where not
         exists`. Un SKU que falló y luego publicó aparece DOS veces; al contar,
         agrupar por SKU y tomar el último.
      2. El error va COMPLETO y literal. "150011019 The input basePrice:null is
         incorrect" sirve para arreglar; "falló el precio" no.
      3. Un fallo al registrar nunca aborta: perder el registro es malo, perder
         la corrida por el registro es peor.
    """
    try:
        ahora = datetime.now(timezone.utc)
        sdb.execute(
            """insert into ops.channel_submissions
                 (canal, cuenta, sku, submission_id, operacion, status, success,
                  error_resumen, detail_ref, submitted_at, published_at)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (CANAL, CUENTA, sku, str(submission_id) if submission_id else None,
             operacion, status or ("published" if ok else "failed"), ok,
             (error or "")[:2000] or None, lote, ahora, ahora if ok else None))
    except Exception as exc:  # noqa: BLE001
        _fallos_bitacora.append(f"{sku}: {str(exc)[:120]}")


async def subir_imagenes(reg: dict, tope: int = 5) -> list[str]:
    """chunche.shop -> JPEG 1000px -> WordPress -> Temu -> url kwcdn.

    Temu rehospeda CONSERVANDO el tamaño que recibe: con la URL cruda quedan en
    800×800; convertidas, en 1000×1000. Se reusa el conversor de
    `imagenes_amazon` (Lanczos + las cabeceras que sortean el WAF de Hostinger).
    La URL kwcdn hay que GUARDARLA: no se puede recalcular.
    """
    urls = []
    for i, u in enumerate((reg.get("imagenes") or [])[:tope], 1):
        try:
            crudo = await ia._descargar(u)
            if not crudo:
                continue
            jpeg, _, _ = await asyncio.to_thread(ia._a_jpeg, crudo, 1000)
            sub = await wc.subir_imagen_wp(f"temu-{reg['sku']}-{i}.jpg", jpeg,
                                           "image/jpeg")
            if not sub:
                continue
            r = await temu.llamar("temu.local.goods.image.v2.upload",
                                  {"fileUrl": sub[1], "usage": 1})
            imgs = r.get("images") or []
            if imgs:
                urls.append(imgs[0]["url"])
        except Exception:  # noqa: BLE001
            continue
    return urls


def armar_payload(reg: dict, imgs: list[str]) -> dict:
    """El payload de `temu.local.goods.v3.add`.

    v3 IGNORA EN SILENCIO lo que no conoce: `catId`, `goodsProperties` y
    `productExpressInfo` (nombres de v1) se descartan sin error. Medido: se pidió
    catId 1761 y publicó en 1769; se mandó 500 g/20×20×20 y guardó el default
    100 g/10×20×30.
    """
    sku = reg["sku"]

    def medida(v, defecto, tope):
        try:
            f = round(float(v or 0), 1)
        except (TypeError, ValueError):
            f = 0.0
        return f"{min(max(f or defecto, 0.1), tope):g}"

    return {
        "language": "es",
        "goodsBasic": {
            "goodsName": reg["titulo"][:500],
            "goodsDesc": reg["descripcion"][:2000],
            "externalGoodsId": sku,
            "extCatName": str(reg["catId"]),       # NO `catId`: v3 no lo conoce
            "shipmentLimitDay": DIAS_DESPACHO,
            "costTemplate": PLANTILLA_FLETE,
            "productType": 1,
            "bulletPoints": reg.get("bullets") or [],
        },
        "attributes": reg.get("atributos") or [],   # {name, value[]}, texto
        "skuList": [{
            "externalSkuId": sku,
            "images": imgs,
            "price": {"basePrice": {"amount": f"{float(reg['precio']):.2f}",
                                    "currency": "MXN"}},
            "quantity": int(reg.get("stock") or 0),
            "packageInfo": {"weight": medida((reg.get("peso") or 0) * 1000, 500.0, 9999999.9),
                            "length": medida(reg.get("largo"), 20.0, 9999.9),
                            "width": medida(reg.get("ancho"), 20.0, 9999.9),
                            "height": medida(reg.get("alto"), 20.0, 9999.9)},
            "variations": [{"name": "Color", "value": (sku.split("-")[-1] or "Standard")[:40]}],
        }],
    }


async def libres(skus: list[str]) -> set[str]:
    """`out.sn.check` en lotes de 50. Sin repetidos: dos veces el mismo SKU en
    la MISMA llamada devuelve `150010003`."""
    skus = list(dict.fromkeys(skus))
    ok: set[str] = set()
    for i in range(0, len(skus), 50):
        try:
            r = await temu.llamar("bg.local.goods.out.sn.check",
                                  {"outGoodsSnList": skus[i:i + 50]})
        except RuntimeError as exc:
            print(f"   ! out.sn.check tanda {i//50+1}: {exc}")
            continue
        for f in (r.get("resultList") or []):
            if not f.get("isDuplicate"):
                ok.add(f["outGoodsSn"])
    return ok


async def fase_publicar(regs: list[dict], lote: str, aplicar: bool) -> None:
    payloads = _leer(PAYLOADS, {})
    publicados = fallidos = 0

    for i, reg in enumerate(regs, 1):
        sku = reg["sku"]
        print(f"[{i}/{len(regs)}] {sku}  {reg['titulo'][:44]}")
        if not aplicar:
            continue

        imgs = await subir_imagenes(reg)
        if not imgs:
            err = "armado: ninguna imagen se pudo convertir/subir"
            print(f"      x {err}")
            registrar(sku, False, lote, error=err)
            fallidos += 1
            continue

        pl = armar_payload(reg, imgs)
        payloads[sku] = pl
        _escribir(PAYLOADS, payloads)      # A DISCO ANTES de mandarlo

        # El detector de Temu, antes de gastar el SKU. OJO: `FAILED` no siempre
        # bloquea — el propio aviso dice "This is only a warning and will not
        # block the submission" (caso "velcro", marca registrada). Se bloquea
        # solo si algún aviso NO se declara a sí mismo como no bloqueante.
        try:
            vr = await temu.llamar("temu.local.goods.illegal.vocabulary.check",
                                   {"goodsName": pl["goodsBasic"]["goodsName"],
                                    "goodsDesc": pl["goodsBasic"]["goodsDesc"]})
        except RuntimeError:
            vr = {}
        if vr.get("checkResult") not in (None, "PASS"):
            avisos = [w for fr in (vr.get("failReasonList") or [])
                      for w in (fr.get("violationWarningContentList") or [])]
            detalle = json.dumps(vr, ensure_ascii=False)[:1500]
            if [w for w in avisos if "will not block" not in (w.get("warning") or "")]:
                print(f"      x vocabulario prohibido (BLOQUEA)")
                registrar(sku, False, lote, error=f"vocabulario prohibido: {detalle}")
                fallidos += 1
                continue
            registrar(sku, True, lote, status="pending",
                      operacion="vocabulary_warning",
                      error=f"aviso NO bloqueante: {detalle}")

        try:
            r = await temu.llamar("temu.local.goods.v3.add", pl)
            gid = r.get("goodsId")
            print(f"      OK goodsId={gid}")
            registrar(sku, True, lote, submission_id=gid)
            publicados += 1
        except RuntimeError as exc:
            print(f"      x {exc}")
            registrar(sku, False, lote, error=str(exc))
            fallidos += 1

    print(f"\n{'='*62}\n  lote {lote}\n  publicados {publicados} · fallidos {fallidos}")
    if _fallos_bitacora:
        print(f"  ⚠ {len(_fallos_bitacora)} fallo(s) al registrar; "
              f"el primero: {_fallos_bitacora[0]}")
    print(f"{'='*62}\n"
          f"  select status, count(distinct sku) from ops.channel_submissions\n"
          f"   where canal='{CANAL}' and detail_ref='{lote}' group by status;")


# ═════════════════════════════════════════════════════════════════════════════
def _pasa_filtro(f: dict) -> bool:
    try:
        p, st = float(f.get("precio") or 0), int(f.get("stock") or 0)
    except (TypeError, ValueError):
        return False
    return st >= MIN_STOCK and MIN_PRECIO <= p < MAX_PRECIO


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("fase", choices=["fichas", "enriquecer", "publicar"])
    ap.add_argument("--skus", help="archivo con los SKU objetivo, uno por línea")
    ap.add_argument("--tanda", type=int, help="bloque de 100")
    ap.add_argument("--limite", type=int)
    ap.add_argument("--aplicar", action="store_true",
                    help="sin esto, `publicar` solo lista (dry-run)")
    ap.add_argument("--lote", default=f"temu:lote:{datetime.now(timezone.utc):%Y%m%d}")
    # QUIÉN corre esto. Las 307 altas que dejó este script en
    # `ops.channel_submissions` no tienen dueño: un script no pasa por el
    # middleware, así que nada firmaba sus filas. Se exige SIEMPRE, incluso en
    # dry-run — una excepción ("salvo cuando no aplica") es exactamente la
    # costura donde se forma la costumbre de saltárselo.
    from core import actor
    actor.agregar_argumento(ap)
    a = ap.parse_args()
    actor.fijar_desde_cli(a.como)   # aborta si no se declaró

    if not temu.disponible():
        print("Temu no está configurado (faltan TEMU_APP_KEY/SECRET/TOKEN).")
        return

    if a.fase == "fichas":
        if not a.skus:
            print("hace falta --skus con el archivo de SKUs objetivo.")
            return
        skus = [s.strip() for s in pathlib.Path(a.skus).read_text(
            encoding="utf-8").replace(",", "\n").split("\n") if s.strip()]
        ya = await libres(skus)
        objetivo = sorted(set(skus) & ya)        # solo los que NO existen en Temu
        print(f"{len(skus)} SKUs · {len(objetivo)} libres en Temu")
        fichas = await asyncio.to_thread(fase_fichas, objetivo)
        _escribir(FICHAS, fichas)
        completas = [f for f in fichas.values() if not f["_falta"]]
        print(f"fichas {len(fichas)} · completas {len(completas)} -> {FICHAS.name}")
        return

    if a.fase == "enriquecer":
        fichas = _leer(FICHAS, {})
        hechos, apart = _leer(ENRIQUECIDO, {}), _leer(APARTADOS, [])
        vistos = set(hechos) | {x["sku"] for x in apart}
        # El filtro va ANTES de la IA: enriquecer algo que el publicador va a
        # descartar es gastar 3 llamadas a DeepSeek para nada.
        univ = sorted((f for f in fichas.values()
                       if not f["_falta"] and _pasa_filtro(f)),
                      key=lambda x: x["sku"])
        pend = [f for f in univ if f["sku"] not in vistos]
        if a.tanda:
            pend = [f for f in univ[(a.tanda - 1) * 100: a.tanda * 100]
                    if f["sku"] not in vistos]
        if a.limite:
            pend = pend[:a.limite]
        print(f"{len(pend)} por enriquecer (de {len(univ)} que pasan el filtro)")

        arbol = _leer(DATOS / "arbol.json", {})
        arbol = {int(k): v for k, v in arbol.items()}
        if not arbol:
            print("⚠ falta _temu/arbol.json (catId -> ruta). Sin él la IA no "
                  "puede juzgar la categoría y se respeta al recomendador.")
        sem = asyncio.Semaphore(5)

        async def uno(f):
            async with sem:
                try:
                    hechos[f["sku"]] = await enriquecer_uno(f, arbol)
                    print(f"  ok {f['sku']}")
                except SinCategoria as exc:
                    apart.append({"sku": f["sku"], "titulo": f["titulo"],
                                  "motivo": str(exc)})
                    print(f"  - APARTADO {f['sku']}: {exc}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  x {f['sku']}: {type(exc).__name__} {str(exc)[:70]}")

        await asyncio.gather(*(uno(f) for f in pend))
        _escribir(ENRIQUECIDO, hechos)
        _escribir(APARTADOS, apart)
        listos = [r for r in hechos.values() if not r["obligatorios_sin_llenar"]]
        print(f"\nenriquecidos {len(hechos)} · listos {len(listos)} · "
              f"apartados {len(apart)}")
        return

    # publicar
    regs = _leer(ENRIQUECIDO, {})
    sel = sorted((r for r in regs.values() if not r["obligatorios_sin_llenar"]),
                 key=lambda r: r["sku"])
    # Stock EN VIVO: el del JSON es una foto y pudo venderse todo. Regla de la
    # casa: los cruces se hacen en vivo.
    vivo = await asyncio.to_thread(wp_db.productos_por_sku, [r["sku"] for r in sel])
    for r in sel:
        v = vivo.get(r["sku"])
        if v and v.get("stock") is not None:
            r["stock"] = v["stock"]
    sel = [r for r in sel if _pasa_filtro(r)]
    disp = await libres([r["sku"] for r in sel])
    sel = [r for r in sel if r["sku"] in disp]
    if a.limite:
        sel = sel[:a.limite]
    print(f"{len(sel)} a publicar" + ("" if a.aplicar else "   [DRY-RUN]"))
    await fase_publicar(sel, a.lote, a.aplicar)


if __name__ == "__main__":
    asyncio.run(main())
