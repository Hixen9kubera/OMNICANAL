"""
media_read.py — Las imágenes ya procesadas, en la BD kubera.

Gemela de la caché de `imagenes_amazon._cache_get`:

    amazon_imagenes  →  enrich.product_media   (kind = 'amazon')

LA LLAVE CAMBIA DE FORMA, Y ESO ES LO ÚNICO DELICADO
----------------------------------------------------
En MySQL la PK es `src_hash` = SHA-1 de la URL de origen, y es **única global**:
la misma imagen usada por dos SKUs es UNA fila. En kubera el índice único es
`(sku, kind, source_url)`, así que esa misma imagen da **DOS** filas.

Por eso esta lectura **NO filtra por SKU**, igual que su gemela: la pregunta es
"¿ya procesé ESTA URL?", no "¿ya la procesé para este SKU?". Filtrar por SKU
haría que la misma imagen se reprocesara una vez por producto.

Y reprocesar no es gratis ni idempotente: bajar la imagen de WordPress,
WebP→JPEG, escalar a ≥1000 px con Lanczos y a veces Real-ESRGAN, y **subir otra
copia a WordPress con otro `wp_media_id`**. Una caché fallada aquí no se paga en
tiempo, se paga en archivos duplicados en la biblioteca de medios.

`limit 1` a propósito: con dos filas para la misma URL las dos traen el mismo
`cdn_url` (es el resultado del mismo procesamiento), así que cualquiera sirve.
"""
from __future__ import annotations

from services import supabase_db as sdb


def imagen_amazon(src_url: str) -> str | None:
    """URL ya procesada para esa imagen de origen, o None si nunca se procesó.

    BLOQUEANTE (psycopg2). No se llama desde una corrutina sin
    `asyncio.to_thread` — regla 11.
    """
    if not (src_url or "").strip():
        return None
    fila = sdb.fetch_one(
        """select cdn_url from enrich.product_media
            where kind = 'amazon' and source_url = %s
              and cdn_url is not null and cdn_url <> ''
            limit 1""", (src_url,))
    return (fila or {}).get("cdn_url") or None
