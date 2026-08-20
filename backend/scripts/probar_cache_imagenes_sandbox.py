"""
probar_cache_imagenes_sandbox.py — La caché de imágenes de Amazon sobrevive al
corte, y no ensucia la galería de WordPress.

QUÉ SE ESTABA MIDIENDO MAL
--------------------------
La LECTURA (`imagenes_amazon._cache_get`) ya estaba migrada y encendida en
producción (`SUPABASE_READ_MEDIA=true`), con paridad exacta: 714 filas de cada
lado, escritas el mismo segundo. Ese lado estaba resuelto.

La ESCRITURA no. `_cache_put` tenía el espejo a `enrich.product_media` DENTRO
del mismo `try` que el `INSERT` a MySQL y DESPUÉS de él, así que un fallo de
MySQL se llevaba el espejo por delante. El día del corte eso significa que la
imagen se procesa, se sube a WordPress y **no queda cacheada en ningún lado**:
en la vuelta siguiente se reprocesa y se sube OTRA copia con otro `wp_media_id`.

Y el `except` registraba en DEBUG — invisible en producción. El daño habría sido
silencioso además de caro.

Cada fila de esa caché es una descarga, una conversión WebP→JPEG, un escalado a
≥1000 px y a veces una pasada por Real-ESRGAN. **No es "se vuelve a consultar".**

LO QUE PRUEBA
-------------
Con `MYSQL_ENABLED=false` —el mundo de después del corte—:

  · `_cache_get` contesta desde kubera
  · `_cache_put` DEJA la imagen en `enrich.product_media` aunque MySQL no exista
  · y lo que dejó se puede volver a leer, que es lo único que evita el reproceso

El ciclo completo, que es la única prueba que vale: guardar → leer → encontrar.

Escribe en el sandbox con marcas `PRUEBA-` y limpia al final. Aborta si el DSN
es de producción.

Uso:
  ...python backend/scripts/probar_cache_imagenes_sandbox.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cargar(nombre: str) -> dict[str, str]:
    d: dict[str, str] = {}
    p = ROOT / nombre
    if not p.exists():
        return d
    for l in p.read_text(encoding="utf-8").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, _, v = s.partition("=")
            d[k.strip()] = v.split(" #")[0].strip().strip('"').strip("'")
    return d


_ST = cargar("env.staging")
if not _ST.get("SUPABASE_DB_URL"):
    sys.exit("ABORT: env.staging sin SUPABASE_DB_URL.")
os.environ["SUPABASE_DB_URL"] = _ST["SUPABASE_DB_URL"]
os.environ["APP_ENV"] = "staging"
os.environ["MYSQL_ENABLED"] = "false"
os.environ["SUPABASE_READ_MEDIA"] = "true"
os.environ["KUBERA_MIRROR_ENABLED"] = "true"
os.environ["KUBERA_DB_URL"] = _ST["SUPABASE_DB_URL"]

from config import settings  # noqa: E402

_ref = (settings.supabase_db_url or "").split("postgres.")[-1].split(":")[0]
if _ref[:8] == "tukwcvsi":
    sys.exit("ABORT: esto apunta a PRODUCCION y este script ESCRIBE.")
if settings.mysql_enabled:
    sys.exit("ABORT: MYSQL_ENABLED quedo encendido.")

from services import imagenes_amazon, media_read  # noqa: E402
from services import supabase_db as sdb  # noqa: E402

_ok = True
# El SKU tiene que EXISTIR en core.products: `enrich.product_media.sku` lleva
# llave foranea. La primera version uso uno inventado y la prueba reprobo por
# eso — midiendo su propio defecto, no el del codigo. Se toma uno real y se
# limpia solo la fila de media que se crea.
_SKU = ""
_SRC = "https://ejemplo.invalido/PRUEBA-imagen-origen.webp"
_CDN = "https://chunche.shop/wp-content/uploads/PRUEBA-imagen.jpg"


def check(etiqueta: str, cond: bool, detalle: str = "") -> None:
    global _ok
    _ok &= bool(cond)
    print(f"  [{'OK  ' if cond else 'FALLA'}] {etiqueta}" + (f" — {detalle}" if detalle else ""))


def limpiar() -> None:
    # Solo la fila de PRUEBA, identificada por su source_url inventada: el SKU
    # es real y sus imagenes de verdad no se tocan.
    sdb.execute("delete from enrich.product_media where source_url = %s", (_SRC,))


def main() -> None:
    global _SKU
    print(f"SANDBOX {_ref[:8]}... · MYSQL_ENABLED={settings.mysql_enabled}\n")
    real = sdb.fetch_one("select sku::text as sku from core.products order by sku limit 1")
    if not real:
        sys.exit("ABORT: el sandbox no tiene productos en core.products. Re-sembrar.")
    _SKU = real["sku"]
    print(f"  SKU real del sandbox: {_SKU}\n")
    limpiar()

    # 1. Antes de nada, la caché no la conoce. False positivo si esto ya diera
    #    algo: estariamos midiendo una fila vieja.
    antes = imagenes_amazon._cache_get(_SRC)
    check("antes de guardar, la cache NO conoce la imagen", antes is None, str(antes))

    # 2. LA PRUEBA: guardar sin MySQL. Antes del arreglo, el espejo iba dentro
    #    del try del INSERT a MySQL, asi que aqui no se guardaba NADA.
    imagenes_amazon._cache_put(_SKU, _SRC, _CDN, media_id=999999,
                               ancho=1200, alto=1200, metodo="prueba")
    # El espejo despacha a un hilo; se le da un momento.
    for _ in range(20):
        if sdb.fetch_one("select 1 from enrich.product_media where source_url=%s", (_SRC,)):
            break
        time.sleep(0.5)

    fila = sdb.fetch_one(
        "select sku::text as sku, kind, source_url, cdn_url from enrich.product_media "
        "where source_url = %s", (_SRC,))
    check("SIN MySQL, _cache_put deja la imagen en enrich.product_media",
          bool(fila),
          "es lo que evita reprocesar y subir otra copia a WordPress"
          if fila else "NO se guardo en ningun lado")
    if fila:
        check("  con el tipo y las dos URLs correctas",
              fila["kind"] == "amazon" and fila["source_url"] == _SRC
              and fila["cdn_url"] == _CDN, str(fila)[:110])

    # 3. Y lo guardado se vuelve a encontrar. Guardar sin poder leer no sirve.
    despues = imagenes_amazon._cache_get(_SRC)
    check("y _cache_get la vuelve a encontrar (el ciclo cierra)",
          despues == _CDN, f"esperado {_CDN}, obtuvo {despues}")

    # 4. La maña de la llave: se pregunta por URL, SIN filtrar por SKU. Si el
    #    repunte filtrara por SKU, la misma imagen usada por otro producto se
    #    reprocesaria — y en MySQL la PK es el hash de la URL, global.
    otra = media_read.imagen_amazon(_SRC)
    check("la busqueda es por URL y NO por SKU", otra == _CDN,
          "en MySQL la PK es el hash de la URL (global); filtrar por SKU "
          "reprocesaria imagenes compartidas")

    # 5. Y que MySQL no se haya tocado en todo esto.
    from services import db
    check("el pool de MySQL nunca se creo", db._pool is None)

    limpiar()
    print(f"\n  (limpieza: las filas PRUEBA- se borraron del sandbox)")
    print(f"\nRESULTADO: {'la cache de imagenes sobrevive al corte' if _ok else 'REVISAR lo marcado FALLA'}")
    sys.exit(0 if _ok else 1)


if __name__ == "__main__":
    main()
