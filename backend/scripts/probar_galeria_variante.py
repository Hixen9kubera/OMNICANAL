"""
probar_galeria_variante.py — Lo que devolvería GET /api/imagenes/{sku} con
GALERIA_VARIANTE encendida, sin encenderla en ningún servidor.

SOLO LECTURA. Lee la base de WordPress (SELECT) y la REST de Woo (GET con
`_cb`). No escribe nada, y no por disciplina sino por candado: antes de empezar
se sustituyen `put/post/patch/delete` de `httpx.AsyncClient` por una función que
REVIENTA, así que si algún camino intentara escribir, el script muere en vez de
escribir. Tampoco arranca la app ni su scheduler (el .env es de producción).

Por cada SKU imprime:
  • la respuesta con el flag APAGADO (la de siempre) y ENCENDIDO;
  • para una NO-variación, si las dos son idénticas (el contrato lo exige);
  • para una variación, las filas crudas de `_thumbnail_id`,
    `_product_image_gallery` y `_kubera_galeria` para cotejar a mano.

Uso:
  ...python -m scripts.probar_galeria_variante                       # 3 SKUs por omisión
  ...python -m scripts.probar_galeria_variante MASC-1022-CAF OTRO-SKU

PRIMERA PRUEBA REAL DE ESCRITURA (no la hace este script; se hace a mano)
──────────────────────────────────────────────────────────────────────────
Lo que NO se ha probado contra la tienda y esa prueba tiene que demostrar:
  (1) que `_kubera_galeria` se actualiza EN SU LUGAR (1 fila tras varias
      escrituras — la tesis de `META_GALERIA_PROPIA`);
  (2) que `{"image": {"id": 0}}` quita la principal en la versión de Woo de
      chunche.shop (leído en el código de trunk, no medido).

Elegir la variación:
  • SIN publicaciones vivas en ningún canal (channel.listings de kubera sin
    filas activas para su SKU; ni ML, ni Amazon, ni TikTok, ni Temu, ni
    Walmart): si algo sale mal, ningún anuncio cambia de foto.
  • SIN `_product_image_gallery` (6,908 de 7,477 variaciones solo tienen
    miniatura): así la reversa deja sus fotos efectivas EXACTAMENTE como
    estaban (ver "Revertir").
  • Con al menos 2 heredadas publicables en el padre (para adoptar y
    reordenar).

Pasos — sin arrancar el backend, desde una consola con el .env de producción,
UN paso a la vez, corriendo este script con ese SKU (y con UNA HERMANA suya)
entre paso y paso. Los pasos 1-3 prueban el ALMACÉN con la escritura de bajo
nivel, que no siembra; los 4-5 prueban el flujo del Estudio, que sí siembra
(la primera escritura que mete algo a la galería copia antes lo que hoy se
publica del padre, para que la variante no publique menos fotos).

  0. `python -m scripts.probar_galeria_variante <SKU> <SKU_HERMANA>` > antes.txt.
     Anotar wc_id, `principal_id` y los ids de 2 heredadas publicables (H1, H2).
  1. fijar_galeria con [H1]:
       python -c "import asyncio; from services import imagenes_variante as v;
                  print(asyncio.run(v.fijar_galeria(<wc_id>, [<H1>])))"
     Esperado: ok True; en la BD UNA fila `_kubera_galeria` = "H1"; en la REST
     (GET /products/{padre}/variations/{id}?_cb=…) la meta aparece CON id.
     Anotar ese meta_id.
  2. fijar_galeria con [H1, H2] → la MISMA fila (mismo meta_id) = "H1,H2".
     SI APARECE UNA SEGUNDA FILA, PARAR: la tesis (1) es falsa y el almacén hay
     que cambiarlo antes de encender nada.
  3. Revertir la galería (ver "Revertir": borrar la fila con value null) y
     correr el paso 0: tiene que salir IGUAL que antes.txt.
  4. Flujo del Estudio — adoptar H1:
       python -c "import asyncio; from services import imagenes_variante as v;
                  print(asyncio.run(v.adoptar(<wc_id>, <H1>)))"
     Esperado: ok True; UNA fila `_kubera_galeria` con el SEMBRADO (las fotos
     del padre que hoy se publican, en su orden, sin la principal); `regla`
     pasa a "propias" con el MISMO número de URLs publicables que antes.txt
     (o una más si H1 no se publicaba). La HERMANA sigue EXACTAMENTE igual que
     en antes.txt: editar una variante no le quita fotos a sus hermanas.
  5. Revertir otra vez y comparar con antes.txt.
  6. (prueba de la tesis 2, opcional) en una variación de prueba cuya única
     foto sea desechable: quitar la principal → `_thumbnail_id` vacío o sin
     fila. Si Woo contesta 400 `woocommerce_variation_invalid_image_id`, la
     función ya lo reporta como ok False y no se escribe nada.

Revertir (en cualquier punto):
  • principal: `asyncio.run(v.fijar_principal(<wc_id>, <principal original>))`.
  • galería, reversa EXACTA: borrar la fila. `fijar_galeria(<wc_id>, [])` NO
    revierte: deja `_kubera_galeria` = "" y una meta vacía MANDA (fuente
    "propia"), así que en una variación CON `_product_image_gallery` su galería
    de Crear dejaría de verse y publicarse. La REST SÍ borra una meta: PUT por
    `woocommerce.ruta_escritura(wc_id)` con
    `{"meta_data": [{"id": <meta_id de _kubera_galeria leído con _cb>,
    "key": "_kubera_galeria", "value": null}]}` — WC_Data::save_meta_data llama
    a delete_meta cuando el valor es null (leído en el código de Woo, no medido
    en chunche.shop). Confirmar con un SELECT que no queda ninguna fila y
    correr el paso 0 contra antes.txt. Si la fila sigue, en una variación SIN
    legado la vacía lee igual que no tenerla (misma `regla`, mismas URLs); en
    una CON legado, avisar antes de seguir.
  • La reversa GLOBAL es apagar GALERIA_VARIANTE: el GET vuelve a la galería
    del padre. Las publicaciones siguen leyendo `_kubera_galeria` si existe
    (`para_publicar`), por eso la prueba va en una variación sin publicaciones.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402

SKUS_POR_OMISION = ["MASC-1022-CAF", "TEC-0664-ROS"]


def _candado_sin_escrituras() -> None:
    """Cualquier escritura HTTP revienta: este script solo puede leer."""
    def _prohibido(self, url, *a, **k):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError(f"ESCRITURA BLOQUEADA en el script de prueba: {url}")
    for metodo in ("put", "post", "patch", "delete"):
        setattr(httpx.AsyncClient, metodo, _prohibido)


def _no_variacion() -> str | None:
    """Un producto SIMPLE publicado con miniatura: el caso 'no-variación'."""
    from services import wp_db
    P = wp_db._prefix()  # noqa: SLF001
    filas = wp_db._fetch_all(  # noqa: SLF001
        f"""SELECT sku.meta_value AS sku
              FROM {P}posts p
              JOIN {P}postmeta sku ON sku.post_id = p.ID AND sku.meta_key = '_sku'
              JOIN {P}postmeta t   ON t.post_id = p.ID AND t.meta_key = '_thumbnail_id'
             WHERE p.post_type = 'product' AND p.post_status = 'publish'
               AND sku.meta_value <> ''
               AND NOT EXISTS (SELECT 1 FROM {P}posts h
                                WHERE h.post_parent = p.ID AND h.post_type = 'product_variation')
             ORDER BY p.ID DESC LIMIT 1""")
    return str(filas[0]["sku"]).strip() if filas else None


def _filas_crudas(wc_id: int) -> dict:
    from services import imagenes_variante as v
    return v._filas_imagen([wc_id]).get(wc_id, {})  # noqa: SLF001


async def _get(sku: str, encendida: bool) -> dict:
    from config import settings
    from routers import imagenes
    antes = settings.galeria_variante
    settings.galeria_variante = encendida  # solo en este proceso
    try:
        return await imagenes.galeria(sku, None)
    finally:
        settings.galeria_variante = antes


async def main() -> int:
    _candado_sin_escrituras()
    from services import imagenes_variante as v, wp_db

    if not wp_db.disponible():
        print("Sin base de WordPress: con el flag encendido el GET cae a la galería "
              "del padre. Nada que probar.")
        return 1
    skus = sys.argv[1:] or SKUS_POR_OMISION + [s for s in [_no_variacion()] if s]
    fallas = 0
    for sku in skus:
        print("=" * 78)
        var = v.resolver(sku, None)
        print(f"{sku}: {'VARIACIÓN wc_id=%s padre=%s' % var if var else 'NO es variación'}")
        apagada = await _get(sku, False)
        encendida = await _get(sku, True)
        if var:
            print("-- filas crudas:", json.dumps(_filas_crudas(var[0]), ensure_ascii=False))
            print("-- flag APAGADO (galería del padre): %d imágenes" % len(apagada.get("imagenes") or []))
            print("-- flag ENCENDIDO:")
            print(json.dumps(encendida, ensure_ascii=False, indent=2))
            if not encendida.get("es_variante"):
                print("!! FALLA: una variación sin es_variante")
                fallas += 1
        else:
            iguales = apagada == encendida
            print(f"-- idéntica con el flag apagado y encendido: {iguales}")
            print(json.dumps(encendida, ensure_ascii=False, indent=2)[:1500])
            if not iguales:
                print("!! FALLA: una no-variación cambió de respuesta")
                fallas += 1
    print("=" * 78)
    print("SIN FALLAS" if not fallas else f"{fallas} FALLA(S)")
    return 1 if fallas else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
