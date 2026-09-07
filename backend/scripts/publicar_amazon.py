r"""
publicar_amazon.py — El alta de Amazon desde la línea de comandos, FIRMADA.

    python -m scripts.publicar_amazon --como brandon@kubera.mx --via claude
    python -m scripts.publicar_amazon --como brandon@kubera.mx --via claude --aplicar

Sin `--aplicar` no publica nada: arma el payload real, lo mide y lo enseña. Es
el mismo `preview` que usa el botón del panel.

POR QUÉ EXISTE
──────────────
El botón del panel publica UN producto a la vez, con el Estudio abierto. Brandon
pidió once, con la mejora de IA antes de cada uno. Hacerlo a mano son once
aperturas del Estudio y once oportunidades de saltarse un paso.

No reimplementa nada: llama a `publicar.preview` / `publicar.confirmar`, que son
las mismas funciones del botón, y a `amazon_ia.mejorar`, que es el mismo
"Mejorar con IA". `vendor/` no se toca.

LAS TRES COSAS QUE EL CAMINO DEL PANEL NO HACÍA
───────────────────────────────────────────────
1. **Filtrar por existencias.** Brandon fue explícito: "sólo envía los productos
   que tengan piezas". El publicador hace lo contrario sin avisar —
   `attribute_mapper.py:178` es `stock = max(_safe_int(...), 1)`, así que un SKU
   con CERO piezas se publica ofertando UNA. La compuerta tiene que estar aquí,
   antes de llamar, y se mide contra **Odoo**, que es el master del inventario
   (regla de la casa desde el 20-ago-2026), no contra la caché de canales.

2. **Traer las fotos de Mercado Libre.** Ocho SIL-00x no tienen ni una imagen en
   WooCommerce: sus tres padres tienen 0 adjuntos y 0 caracteres de descripción,
   y Odoo tampoco guarda foto de ellos. Lo único que existe son sus anuncios de
   ML. Se leen de ahí y se pasan por `campos["imagenes"]`, que
   `publicar_ready.construir_prod` respeta desde v0.421.0.

   OJO CON LA VARIANTE QUE SE PIDE. El `secure_url` que trae el anuncio es la de
   **500 px**, aunque el campo `max_size` diga 1024. La grande se pide aparte,
   con `GET /pictures/{id}`, y se elige la variación de mayor área. Pedir la del
   anuncio devuelve imágenes que Amazon rechaza por el mínimo de 1000 px.

3. **Firmar quién lo corrió.** `--como` es obligatorio y `--via` viaja con él.
   Los otros dos publicadores de scripts llaman `fijar_desde_cli(a.como)` y
   TIRAN el `--via`, así que hoy no hay una sola fila que pueda decir "lo corrió
   un chat". Aquí se pasan los dos.

LO QUE NO HACE
──────────────
No elige el tipo de producto de Amazon. Esa es la regla 2 de la casa: la
elección del panel MANDA sobre cualquier detector, y el detector automático
—tres primeras palabras del título contra un índice en inglés, con `HOME` de
respaldo— ya publicó una máquina sexual como máquina de coser. Si un SKU no
tiene tipo ni en su variante ni en su padre, este script lo SALTA y lo dice.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.request
from typing import Any

try:  # la consola de Windows es cp1252 y este informe lleva acentos y flechas
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import amazon_ia, meli, publicar, publicar_ready, wp_db  # noqa: E402

# Los once que Brandon mandó publicar el 7-sep-2026. Se pueden pisar con --skus.
OBJETIVO = [
    "SIL-008-GRI", "SIL-008-NEG",
    "SIL-009-NEG", "SIL-009-AZU", "SIL-009-GRI",
    "SIL-011-GRI", "SIL-011-NEG",
    "TEC-0935-ROS", "TEC-0935-AZLMAR",
    "CAM-0030-QUE", "CAM-0030-MAT",
]

# SKUs que toman prestada la foto de otro. **Es una decisión declarada de
# Brandon** (7-sep-2026), no una inferencia: SIL-011-NEG tiene 50 piezas —la
# mayor existencia de su familia— y no tiene anuncio en ML ni foto en ninguna
# parte, así que se publica con la del gris. Queda escrito aquí para que quien
# lo lea después sepa que la foto no corresponde al color, y por qué.
FOTO_PRESTADA = {"SIL-011-NEG": "SIL-011-GRI"}


# ── Existencias, desde Odoo ───────────────────────────────────────────────────
def stock_odoo(skus: list[str]) -> dict[str, float]:
    """
    `free_qty` por SKU, en Odoo, que es el MASTER del inventario.

    NO se usa `odoo.stock_por_sku`, que existe y sería lo cómodo: esa devuelve
    `qty_available`, y aquí eso mentiría. `qty_available` cuenta lo que está en
    el almacén; `free_qty` descuenta además lo COMPROMETIDO en borradores. La
    diferencia no es teórica — el caso VIA-0024-NEG tenía 30 piezas con 29
    comprometidas, o sea UNA vendible, y Woo ofrecía 14. Publicar en Amazon con
    `qty_available` es prometer mercancía que ya tiene dueño.

    Se reutilizan los ayudantes de conexión de `services/odoo.py` para no abrir
    un segundo camino a Odoo con su propia configuración.
    """
    from config import settings
    from services import odoo

    uid = odoo._uid()
    if not uid or not skus:
        return {}
    filas = odoo._models().execute_kw(
        settings.odoo_db, uid, settings.odoo_password,
        "product.product", "search_read",
        [[["default_code", "in", skus]]],
        {"fields": ["default_code", "free_qty"]},
    )
    return {f["default_code"]: float(f.get("free_qty") or 0)
            for f in filas if f.get("default_code")}


# ── Fotos y texto desde el anuncio de Mercado Libre ───────────────────────────
def _ml_api(ruta: str, token: str) -> Any:
    pet = urllib.request.Request(
        f"https://api.mercadolibre.com{ruta}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "kubera-omnicanal"})
    with urllib.request.urlopen(pet, timeout=30) as r:
        return json.loads(r.read())


def _area(medida: str | None) -> int:
    try:
        an, al = str(medida).split("x")
        return int(an) * int(al)
    except Exception:  # noqa: BLE001
        return 0


def listing_ml(sku: str) -> str | None:
    """El `listing_id` de ML de ese SKU, desde `channel.listings`."""
    from services import supabase_db as sdb

    with sdb.get_cursor() as cur:
        cur.execute("""select listing_id from channel.listings
                        where sku = %s and canal = 'mercado_libre'
                          and listing_id is not null limit 1""", (sku,))
        fila = cur.fetchone()
    if not fila:
        return None
    return fila[0] if isinstance(fila, (tuple, list)) else fila.get("listing_id")


def desde_ml(sku: str, token: str) -> dict[str, Any]:
    """
    (imagenes, titulo, descripcion) del anuncio de ML de ese SKU.

    Las imágenes se piden en su variación MÁS GRANDE, no la del anuncio: ver la
    advertencia de la cabecera. Devuelve dict vacío si el SKU no tiene anuncio.
    """
    item_id = listing_ml(sku)
    if not item_id:
        return {}
    item = _ml_api(f"/items/{item_id}", token)
    urls: list[str] = []
    for foto in (item.get("pictures") or []):
        try:
            variaciones = _ml_api(f"/pictures/{foto['id']}", token).get("variations") or []
        except Exception:  # noqa: BLE001
            variaciones = []
        if not variaciones:
            continue
        mejor = max(variaciones, key=lambda v: _area(v.get("size")))
        u = mejor.get("secure_url") or mejor.get("url")
        if u:
            urls.append(u)
    try:
        descripcion = (_ml_api(f"/items/{item_id}/description", token) or {}).get("plain_text") or ""
    except Exception:  # noqa: BLE001
        descripcion = ""
    return {"item_id": item_id, "imagenes": urls,
            "titulo": item.get("title") or "", "descripcion": descripcion}


# ── El alta ───────────────────────────────────────────────────────────────────
async def preparar(sku: str, wc_id: int, token_ml: str | None,
                   con_ia: bool) -> dict[str, Any]:
    """
    Arma el `req` que reciben `preview` y `confirmar`, igual que lo mandaría el
    Estudio. Devuelve además las notas de lo que se hizo, para el informe.

    La ficha se lee con `publicar_ready.construir_prod`, que es EL MISMO lector
    que usa el publicador: título, precio, descripción e imágenes salen de ahí,
    así que lo que se simula es lo que se va a mandar. Armar la ficha por
    separado sería abrir la puerta a que el ensayo y el alta difieran.
    """
    notas: list[str] = []
    prod = await asyncio.to_thread(publicar_ready.construir_prod, sku, wc_id, {})
    ficha = {"titulo": prod.get("title") or "",
             "descripcion": prod.get("description") or "",
             "precio": prod.get("price")}

    imagenes = list(prod.get("images") or [])
    if imagenes:
        notas.append(f"{len(imagenes)} imágenes de WooCommerce")

    # Sin foto en Woo: se va a buscar al anuncio de ML — el de este SKU, o el
    # del que Brandon declaró como préstamo.
    if not imagenes and token_ml:
        fuente = FOTO_PRESTADA.get(sku, sku)
        ml = desde_ml(fuente, token_ml)
        imagenes = ml.get("imagenes") or []
        if imagenes:
            de_quien = "" if fuente == sku else f" (PRESTADAS de {fuente})"
            notas.append(f"{len(imagenes)} imágenes de ML {ml['item_id']}{de_quien}")
        if not (ficha.get("descripcion") or "").strip() and ml.get("descripcion"):
            ficha["descripcion"] = ml["descripcion"]
            notas.append("descripción tomada de ML")

    campos: dict[str, Any] = {
        "titulo": ficha.get("titulo") or "",
        "descripcion": ficha.get("descripcion") or "",
        "precio_regular": ficha.get("precio"),
    }

    if con_ia:
        mejora = await amazon_ia.mejorar({
            "sku": sku, "wc_id": wc_id,
            "nombre": ficha.get("titulo") or "",
            "descripcion": ficha.get("descripcion") or "",
            "precio": ficha.get("precio") or 0,
            "atributos": wp_db.atributos(wc_id),
        }, guardar=True)
        generado = mejora.get("campos") or {}
        campos.update({k: v for k, v in generado.items() if v})
        if mejora.get("rechazados"):
            notas.append(f"IA: {len(mejora['rechazados'])} campos RECHAZADOS por el validador")
        if mejora.get("avisos"):
            notas.append(f"IA: {len(mejora['avisos'])} avisos")
        notas.append("contenido mejorado con IA y guardado en enrich.channel_content")

    # Las imágenes van al final para que la IA no las pise.
    campos["imagenes"] = imagenes
    return {"req": {"canal": "amazon", "sku": sku, "wc_id": wc_id, "campos": campos},
            "notas": notas, "imagenes": len(imagenes)}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skus", help="lista separada por comas; por omisión, los once del 7-sep")
    ap.add_argument("--aplicar", action="store_true",
                    help="sin esto solo simula (preview), no publica")
    ap.add_argument("--sin-ia", action="store_true",
                    help="salta la mejora con IA (para reintentar sin volver a gastarla)")
    ap.add_argument("--min-piezas", type=float, default=1.0,
                    help="mínimo de free_qty en Odoo para publicar (por omisión 1)")

    # QUIÉN y POR DÓNDE. Se exige siempre, también en simulación: la excepción
    # "salvo cuando no aplica" es la costura donde se forma la costumbre de
    # saltárselo. Y `--via` SÍ se pasa, a diferencia de los otros dos scripts.
    from core import actor
    actor.agregar_argumento(ap)
    a = ap.parse_args()
    quien = actor.fijar_desde_cli(a.como, a.via)

    skus = [s.strip() for s in (a.skus or "").split(",") if s.strip()] or list(OBJETIVO)

    print("=" * 74)
    print(f"AMAZON · {len(skus)} SKUs · {'PUBLICANDO' if a.aplicar else 'SIMULACIÓN'}")
    print(f"corre: {quien or '(sin firma)'} · vía: {actor.origen_actual()}")
    print("=" * 74)

    # ── 1. La compuerta de existencias ────────────────────────────────────────
    piezas = await asyncio.to_thread(stock_odoo, skus)
    con_piezas, sin_piezas = [], []
    for s in skus:
        (con_piezas if piezas.get(s, 0) >= a.min_piezas else sin_piezas).append(s)
    for s in sin_piezas:
        cuantas = piezas.get(s)
        print(f"  — {s:17} SE SALTA: {'no existe en Odoo' if cuantas is None else f'{cuantas:g} piezas'}")
    if not con_piezas:
        print("\nNinguno tiene piezas. No hay nada que publicar.")
        return 0

    # ── 2. Sus fichas de WooCommerce ──────────────────────────────────────────
    fichas = await asyncio.to_thread(wp_db.productos_por_sku, con_piezas)
    token_ml = None
    try:
        token_ml = meli._access_token("BEKURA") or meli._access_token(None)
    except Exception as exc:  # noqa: BLE001
        print(f"  (sin token de ML: {type(exc).__name__} — no se podrán traer fotos de allá)")

    resultados: list[dict[str, Any]] = []
    for sku in con_piezas:
        ficha = fichas.get(sku)
        if not ficha:
            print(f"  — {sku:17} SE SALTA: no está en WooCommerce")
            continue

        tipo, origen_tipo = publicar._pt_resuelto(sku, ficha.get("wc_id"))
        if not tipo:
            print(f"  — {sku:17} SE SALTA: sin tipo de producto de Amazon. "
                  f"Elígelo en el panel (manda sobre el detector).")
            continue

        armado = await preparar(sku, int(ficha["wc_id"]), token_ml, con_ia=not a.sin_ia)
        req = armado["req"]

        if not armado["imagenes"]:
            print(f"  — {sku:17} SE SALTA: sin ninguna imagen. Amazon exige la principal.")
            continue

        etiqueta = f"  · {sku:17} {tipo} ({origen_tipo}) · {armado['imagenes']} fotos"
        if a.aplicar:
            r = await publicar.confirmar(req)
            estado = "OK" if r.get("ok") else f"FALLÓ · {r.get('error')}"
            print(f"{etiqueta} → {estado}")
        else:
            r = await publicar.preview(req)
            avisos = r.get("avisos") or []
            print(f"{etiqueta} → simulado; {len(avisos)} avisos")
            for av in avisos[:4]:
                print(f"        · {av}")
        for n in armado["notas"]:
            print(f"        {n}")
        resultados.append({"sku": sku, "ok": bool(r.get("ok")), "detalle": r})

    bien = sum(1 for r in resultados if r["ok"])
    print("\n" + "=" * 74)
    print(f"{bien} de {len(resultados)} {'publicados' if a.aplicar else 'simulados sin error'}"
          f" · {len(sin_piezas)} saltados por existencias")
    if a.aplicar:
        print("La bitácora de cada intento queda en `amazon_backlog` (payload y respuesta completos).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
