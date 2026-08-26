"""
probar_corte_total.py — ¿Se puede apagar MySQL DE GOLPE? La lista de lo que se
rompe, medida en vez de estimada.

LA PREGUNTA
-----------
El plan de migración va paso por paso: repuntar un grupo de lectores, verificar,
encender, siguiente. Eduardo preguntó si se puede pasar el corte de una sola vez
y dejar de depender de MySQL ya.

Este script contesta con evidencia, no con opinión. El sandbox corre con
`MYSQL_ENABLED=false`: **ya es el mundo de después del retiro**. Se prenden
TODAS las banderas `supabase_read_*` a la vez —el corte total— y se recorre lo
que el panel usa de verdad. Lo que truena, truena aquí y no en producción.

CÓMO LEER EL RESULTADO
----------------------
  [PASA]  el camino ya vive sin MySQL
  [VACIO] no truena, pero contesta vacío  ← EL PELIGROSO
  [TRUENA] falla ruidosamente

**El renglón que importa es VACIO.** Un camino que truena se arregla porque se
ve; uno que contesta vacío se ve igual que "no hay nada" y ahí es donde este
proyecto ya perdió dinero: la tabla `pedidos_ml` congelada contestaba "esa orden
no existe" con total seguridad, y nacieron 964 pedidos fantasma en 4 h 17 min.

SOLO SE LLAMAN FUNCIONES DE LECTURA. Está escrito una por una abajo, a propósito:
llamar a un servicio "para ver qué pasa" puede escribir (`ventas_ml.resumen`
refresca su caché), y esa lección ya se pagó una vez en esta migración.

Uso:
  ...python backend/scripts/probar_corte_total.py
"""
from __future__ import annotations

import os
import sys
import traceback
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
os.environ["MYSQL_ENABLED"] = "false"
os.environ["APP_ENV"] = "staging"

from config import settings  # noqa: E402

if (settings.supabase_db_url or "").split("postgres.")[-1][:8] == "tukwcvsi":
    sys.exit("ABORT: esto apunta a PRODUCCION.")
if settings.mysql_enabled:
    sys.exit("ABORT: MYSQL_ENABLED quedo encendido.")

# EL CORTE TOTAL: todas las banderas, a la vez — las de LECTURA y tambien las de
# ESCRITURA.
#
# Las de escritura no escriben nada por si solas (aqui solo se llaman lectores),
# pero hay lecturas colgadas de ellas: `costos._comision_categoria_db` solo
# consulta kubera si `costing_write.activo()`, que mira
# `supabase_write_costing`. Dejarlas apagadas hacia que la sonda reportara VACIO
# un camino que en produccion funciona — una falsa alarma, que gasta igual de
# caro que un falso verde. Y sin `supabase_write_tokens` kubera nunca tendria el
# par de tokens y se mediria una tabla vacia en vez del mecanismo.
_BANDERAS = [n for n in dir(settings)
             if n.startswith(("supabase_read_", "supabase_write_"))]
for n in _BANDERAS:
    setattr(settings, n, True)

_RES: list[tuple[str, str, str]] = []

# ── EL MAPA DE PUNTOS CIEGOS ────────────────────────────────────────────────
# Los 26 lugares donde una lectura del MySQL QUE SE RETIRA contesta un valor
# FALSO si falla (`except -> return False/None/[]/{}/0`). Sacados con un
# recorrido del arbol de sintaxis, no a ojo: cada `try` que llama `db.fetch_*`
# y cuyo `except` devuelve algo vacio. Quedan fuera los que leen WordPress
# (`wp_db`), que no se retira.
#
# Este mapa existe porque la sonda no sabia lo que NO miraba. El 25-ago se
# encontro A MANO un punto ciego --`_ya_compensado` desde la cancelacion-- que
# la sonda daba por cubierto sin haberlo tocado nunca. Una sonda que reporta
# 14/14 sin decir sobre cuantos, miente igual que un lector que contesta "no
# hay" cuando quiso decir "no pude preguntar".
#
# estado: "sondeado"  -> hay una sonda abajo con ese mismo nombre
#         "no_aplica" -> deliberadamente fuera, con razon
_MAPA: list[tuple[str, str, str]] = [
    # ── deciden: si contestan mal, se mueve mercancia o dinero ──────────────
    ("pedidos_ml._ya_compensado",            "sondeado", ""),
    ("stock_full._ya_procesada",             "sondeado", ""),
    ("meli._access_token",                   "sondeado", ""),
    ("tiktok.access_token",                  "sondeado", ""),
    ("tiktok.cipher",                        "sondeado", ""),
    ("imagenes_amazon._cache_get",           "sondeado", ""),
    ("costos._comision_categoria_db",        "sondeado", ""),
    ("publicar._ml_publicaciones",           "sondeado", ""),
    ("publicar._product_type_amazon",        "sondeado", ""),
    ("studio._categoria_mysql",              "sondeado", ""),
    ("crear_producto._categoria_curada",     "sondeado", ""),
    # ── muestran: molesto y visible, no caro ────────────────────────────────
    ("inventario.leer_inventario",           "sondeado", ""),
    ("meli.contar_publicados",               "sondeado", ""),
    ("amazon.contar_publicados",             "sondeado", ""),
    ("competencia_captura._nuestras_publicaciones", "sondeado", ""),
    ("fanout_stock.historial",               "sondeado", ""),
    ("fanout_stock.resumen",                 "sondeado", ""),
    ("packing_comparador.buscar_contenedor", "sondeado", ""),
    ("packing_comparador.candidatos",        "sondeado", ""),
    ("packing_comparador.buscar_sku",        "sondeado", ""),
    ("alertas._fila",                        "sondeado", ""),
    # ── fuera, a proposito ──────────────────────────────────────────────────
    ("inventario._sync_ml_sku",     "no_aplica",
     "llama a la API VIVA de Mercado Libre; una sonda no debe salir a la red"),
    ("inventario._sync_amazon_sku", "no_aplica",
     "llama a la API VIVA de Amazon SP-API, idem"),
    ("kubera_mirror.errores_agrupados", "no_aplica",
     "el registro de errores del espejo vive en MySQL A PROPOSITO: es la red que"
     " avisa si kubera se cae. Se pierde el dia del corte, y esta asumido"),
    ("ventas_ml._pedidos_rango", "no_aplica",
     "cache regenerable de la API de ML, detenida (VENTAS_ML_REFRESH=false)"),
    ("alertas._revisar_tokens_rancios", "no_aplica",
     "lo cubre alertas._fila: mismo lector, misma tabla"),
    ("alertas._revisar_token_tiktok", "no_aplica", "idem"),
]


def sin_dato(nombre: str, porque: str) -> None:
    """El sandbox no tiene con que preguntar. NO es un PASA.

    Una sonda que no pudo correr y se cuenta como verde es exactamente el
    defecto que este script persigue, cometido por el script mismo.
    """
    _RES.append((nombre, "SINDATO", porque))


def sonda(nombre: str, fn, vacio=lambda r: not r) -> None:
    """Corre una lectura y clasifica: PASA / VACIO / TRUENA / SINDATO."""
    try:
        r = fn()
    except Exception as exc:  # noqa: BLE001
        linea = ""
        for m in reversed(traceback.extract_tb(exc.__traceback__)):
            if "backend" in m.filename and "scripts" not in m.filename:
                linea = f"{Path(m.filename).name}:{m.lineno}"
                break
        _RES.append((nombre, "TRUENA", f"{type(exc).__name__}: {str(exc)[:70]} @ {linea}"))
        return
    if vacio(r):
        _RES.append((nombre, "VACIO", f"devolvio {str(r)[:60]}"))
    else:
        n = len(r) if hasattr(r, "__len__") else r
        _RES.append((nombre, "PASA", f"{n} …" if not isinstance(n, int) else f"{n}"))


def main() -> None:
    print(f"CORTE TOTAL simulado — sandbox, MySQL apagado, "
          f"{len(_BANDERAS)} banderas de lectura en true\n")

    from services import (amazon, channel_read, costing_read, inventario,  # noqa
                          meli, presencia, studio)

    # SKUs reales del sandbox para que las sondas tengan con qué trabajar.
    from services import supabase_db as sdb
    skus = [f["sku"] for f in sdb.fetch_all(
        """select sku::text as sku from channel.listings
            where canal='mercado_libre' and nullif(listing_id,'') is not null
            group by 1 order by 1 limit 5""")]
    sku = skus[0] if skus else "NO-HAY"

    # ── Lo repuntado (bloques 1 y 2) ────────────────────────────────────────
    print("── lo que ya se repunto ──")
    sonda("meli.listar (rejilla ML)", lambda: meli.listar(page=1, per_page=5)[0])
    sonda("meli.contar_publicados", lambda: meli.contar_publicados(), lambda r: not r)
    sonda("amazon.listar (rejilla Amazon)", lambda: amazon.listar(page=1, per_page=5)[0])
    sonda("amazon.contar_publicados", lambda: amazon.contar_publicados(), lambda r: not r)
    sonda("studio.estado_publicacion", lambda: studio.estado_publicacion(sku),
          lambda r: not r["ml"] and not r["amazon"]["publicado"])
    sonda("presencia.presencia_por_sku", lambda: presencia.presencia_por_sku(skus))
    sonda("channel_read.presencia", lambda: channel_read.presencia(skus))

    # ── Lo que NO se ha tocado (bloques 3 y 4, y los otros pasos) ───────────
    print("── lo que falta ──")
    sonda("inventario.leer_inventario", lambda: inventario.leer_inventario(skus))
    sonda("costing_read.precios_de", lambda: costing_read.precios_de(skus))
    sonda("channel_read.stock_fba_amazon (semilla del vigilante FBA)",
          lambda: channel_read.stock_fba_amazon())

    from services import competencia_captura, orders_write, pedidos_ml, stock_full
    sonda("orders_write.wc_order_id_previo (candado de idempotencia)",
          lambda: orders_write.wc_order_id_previo("0000000000"),
          lambda r: False)
    sonda("competencia_captura._nuestras_publicaciones",
          lambda: competencia_captura._nuestras_publicaciones())

    # ── Lo que decide, y que la sonda no miraba ────────────────────────────
    # Ampliacion del 25-ago. El punto ciego que se encontro a mano
    # (`_ya_compensado` desde la cancelacion) no era una funcion nueva: era una
    # ya conocida a la que nadie le preguntaba. De ahi el mapa de arriba.
    print("── lo que DECIDE (y no se sondeaba) ──")

    def _uno(sql: str):
        """Un dato real del sandbox, o None. Preguntar en seco no prueba nada."""
        try:
            f = sdb.fetch_all(sql)
            return list(f[0].values())[0] if f else None
        except Exception:  # noqa: BLE001
            return None

    from services import costos, crear_producto, imagenes_amazon, publicar
    from services import fanout_stock, packing_comparador, alertas, tiktok

    sonda("publicar._ml_publicaciones (¿actualizo o creo?)",
          lambda: publicar._ml_publicaciones(sku))
    sonda("publicar._product_type_amazon", lambda: publicar._product_type_amazon(sku),
          lambda r: r is None)

    # La categoria de publicar se lee de `categorias_ml` (MySQL) y esos dos
    # lectores NO tienen camino a kubera: ninguna bandera los desvia. Se les
    # pregunta con un SKU real a proposito — si contestan vacio no es que falte
    # el dato, es que el camino no existe.
    sonda("studio._categoria_mysql", lambda: studio._categoria_mysql(sku),
          lambda r: not r)
    sonda("crear_producto._categoria_curada",
          lambda: crear_producto._categoria_curada(sku), lambda r: not r)

    cat = _uno("""select ml_cat_id::text from costing.costos_finales
                   where pct_comision > 0 and nullif(ml_cat_id,'') is not null limit 1""")
    if cat:
        sonda("costos._comision_categoria_db (el precio sale de aqui)",
              lambda: costos._comision_categoria_db(cat), lambda r: not r)
    else:
        sin_dato("costos._comision_categoria_db",
                 "el sandbox no tiene ninguna categoria con comision")

    img = _uno("""select source_url from enrich.product_media
                   where kind='amazon' and nullif(source_url,'') is not null limit 1""")
    if img:
        # Un fallo aqui NO es incorrecto, es caro: se reprocesa la imagen y se
        # vuelve a subir a WordPress con otro media_id. Huerfanos y factura.
        sonda("imagenes_amazon._cache_get (¿ya subi esta imagen?)",
              lambda: imagenes_amazon._cache_get(img), lambda r: not r)
    else:
        sin_dato("imagenes_amazon._cache_get",
                 "el sandbox no tiene ninguna imagen de Amazon cacheada")

    # ── Lo que se ve en pantalla ───────────────────────────────────────────
    print("── lo que se MUESTRA (molesto y visible, no caro) ──")
    sonda("fanout_stock.historial", lambda: fanout_stock.historial(limite=5))
    sonda("fanout_stock.resumen", lambda: fanout_stock.resumen())
    sonda("alertas._fila", lambda: alertas._fila("stock"), lambda r: r is None)

    cont = _uno("""select contenedor::text from costing.costos_validados
                    where nullif(contenedor,'') is not null limit 1""")
    if cont:
        sonda("packing_comparador.buscar_contenedor",
              lambda: packing_comparador.buscar_contenedor(str(cont)[:4]))
        sonda("packing_comparador.candidatos",
              lambda: packing_comparador.candidatos(str(cont)))
    else:
        sin_dato("packing_comparador.buscar_contenedor",
                 "el sandbox no tiene contenedores costeados")
        sin_dato("packing_comparador.candidatos", "idem")
    sonda("packing_comparador.buscar_sku", lambda: packing_comparador.buscar_sku(sku))

    # ── Las sondas que hay que hacer CON DATO ───────────────────────────────
    # Preguntar en seco no sirve: `_ya_procesada` de una operacion que no
    # existe devuelve False, y ese False es la respuesta CORRECTA. Una sonda que
    # no distingue "no existe el camino" de "la tabla esta vacia" comete el
    # mismo error que persigue — asi que aqui se SIEMBRA, se pregunta y se
    # limpia. Es la unica forma de que un candado se pueda sondear.
    from services import candados_read, tokens_read, meli as _m
    _OP = "SONDA-corte-total"
    _CTA = "SONDA-CUENTA"
    _SHOP = "SONDA-tienda-tiktok"
    _EXT = "SONDA-pedido-compensado"
    _WC = 99999902
    try:
        candados_read.marcar_aplicada(_OP, "SONDA-SKU", "AMAZON", "fba_ingreso")
        sonda("stock_full._ya_procesada (candado de bodega — PASO 0)",
              lambda: stock_full._ya_procesada(_OP),
              # Con la operacion YA sellada, la respuesta correcta es True.
              # False aqui significaria que el candado no recuerda: mercancia
              # movida dos veces.
              lambda r: r is not True)
        f = _m._fernet()
        tokens_read.guardar(_CTA, _m._enc(f, "SONDA-no-es-un-token"),
                            _m._enc(f, "SONDA-refresh"))
        sonda("meli._access_token (LOS TOKENS — paso 6)",
              lambda: _m._access_token(_CTA), lambda r: not r)

        # El candado de la CANCELACION. Este es el que se encontro a mano el
        # 25-ago: se preguntaba sin cuenta ni order_id, asi que ni intentaba
        # kubera. Con el pedido sellado como compensado, la respuesta correcta
        # es True; un False significa que la reversion no se va a disparar.
        # `marcar_compensado` solo hace UPDATE: sin la fila no sella nada y la
        # sonda daria False creyendo haber probado algo. Se crea el pedido
        # primero — es la misma disciplina que el candado de bodega de arriba.
        sdb.execute("""insert into channel.orders
                         (canal, cuenta, external_order_id, wc_order_id)
                       values ('mercado_libre', %s, %s, %s)
                       on conflict do nothing""", (_CTA, _EXT, _WC))
        if candados_read.marcar_compensado("mercado_libre", _CTA, _EXT) != 1:
            sin_dato("pedidos_ml._ya_compensado (candado de cancelacion)",
                     "no se pudo sembrar el pedido de prueba en channel.orders")
        else:
            sonda("pedidos_ml._ya_compensado (candado de cancelacion)",
                  lambda: pedidos_ml._ya_compensado(_WC, _CTA, _EXT),
                  lambda r: r is not True)

        # TikTok. `cipher` FALLA DISFRAZADO: sin el, un token valido recibe
        # "shop_cipher is required" y parece un problema de permisos. Por eso
        # se sondea aparte del token y no junto con el.
        tokens_read.tiktok_guardar(
            _SHOP, tiktok._cifrar_o_no("SONDA-no-es-un-token"),
            seller_name="SONDA", open_id="SONDA-oid",
            shop_cipher="SONDA-cipher",
            refresh_token=tiktok._cifrar_o_no("SONDA-refresh"))
        sonda("tiktok.access_token", lambda: tiktok.access_token(_SHOP), lambda r: not r)
        sonda("tiktok.cipher (el que falla disfrazado)",
              lambda: tiktok.cipher(_SHOP), lambda r: not r)
    finally:
        sdb.execute("delete from ops.fulfillment_operations where operacion_id=%s", (_OP,))
        sdb.execute("delete from ops.ml_tokens where cuenta=%s", (_CTA,))
        sdb.execute("delete from ops.tiktok_tokens where shop_id=%s", (_SHOP,))
        sdb.execute("""delete from channel.orders where canal='mercado_libre'
                        and cuenta=%s and external_order_id=%s""", (_CTA, _EXT))

    # ── Reporte ─────────────────────────────────────────────────────────────
    print()
    print("=" * 74)
    orden = {"TRUENA": 0, "VACIO": 1, "SINDATO": 2, "PASA": 3}
    marca = {"PASA": "[PASA]  ", "VACIO": "[VACIO] ",
             "TRUENA": "[TRUENA]", "SINDATO": "[S/DATO]"}
    for nombre, estado, det in sorted(_RES, key=lambda x: orden[x[1]]):
        print(f"  {marca[estado]} {nombre:46s} {det}")
    print("=" * 74)

    n = {e: sum(1 for r in _RES if r[1] == e) for e in orden}
    print()
    print(f"  PASA {n['PASA']}   ·   VACIO {n['VACIO']}   ·   "
          f"TRUENA {n['TRUENA']}   ·   SIN DATO {n['SINDATO']}")
    print()

    # ── COBERTURA: de que tamano es lo que NO se miro ───────────────────────
    # La sonda tiene que decir sobre cuantos. "14 de 14" sin denominador es la
    # misma mentira que un lector que contesta "no hay" queriendo decir "no
    # pude preguntar" — solo que cometida por el que vigila.
    corridas = {nombre for nombre, _, _ in _RES}

    def _corrio(clave: str) -> bool:
        return any(c.startswith(clave) for c in corridas)

    esperadas = [m for m in _MAPA if m[1] == "sondeado"]
    fuera = [m for m in _MAPA if m[1] == "no_aplica"]
    faltan = [m for m in esperadas if not _corrio(m[0])]

    print(f"  COBERTURA: {len(esperadas) - len(faltan)} de {len(_MAPA)} lugares donde una "
          f"lectura del")
    print(f"  MySQL que se retira contesta un valor FALSO si falla.")
    if faltan:
        print()
        print(f"  EL MAPA DICE SONDEADO Y NO SE SONDEO ({len(faltan)}):")
        for clave, _, _ in faltan:
            print(f"     · {clave}")
        print("  (o se le cambio el nombre a la sonda, o se borro: revisar)")
    if fuera:
        print()
        print(f"  FUERA A PROPOSITO ({len(fuera)}):")
        for clave, _, razon in fuera:
            print(f"     · {clave}")
            print(f"       {razon}")

    if n["SINDATO"]:
        print()
        print("  Los SIN DATO no son verdes: son sondas que no pudieron correr")
        print("  porque el sandbox no tenia con que preguntar. Contarlas como")
        print("  PASA seria cometer, aqui dentro, el defecto que este script busca.")
    if n["VACIO"]:
        print()
        print("  Los VACIO son los caros: no avisan. Cada uno es un lugar donde el")
        print("  panel diria «no hay» en vez de «no pude preguntar».")

    print()
    print("  VEREDICTO SOBRE EL CORTE DE GOLPE:")
    malos = n["VACIO"] + n["TRUENA"]
    if malos == 0 and n["SINDATO"] == 0 and not faltan:
        print(f"  Los {len(esperadas)} caminos que deciden o muestran algo viven sin MySQL,")
        print(f"  y los {len(fuera)} que quedan fuera estan nombrados arriba con su razon.")
        print("  Lo que este script NO puede ver: los caminos que solo se ejercitan")
        print("  con trafico real (webhooks, scheduler) y las escrituras.")
    else:
        detalle = f"{malos} camino(s) no sobreviven"
        if n["SINDATO"]:
            detalle += f", {n['SINDATO']} sin poder medirse"
        if faltan:
            detalle += f", {len(faltan)} sonda(s) perdida(s)"
        print(f"  NO todavia: {detalle}.")
        print("  La lista de arriba es el trabajo que falta, en orden.")
    # Codigo de salida distinto de cero cuando algo no sobrevive: asi se puede
    # encadenar y no depende de que alguien lea el texto.
    sys.exit(0 if (malos == 0 and n["SINDATO"] == 0 and not faltan) else 1)


if __name__ == "__main__":
    main()
