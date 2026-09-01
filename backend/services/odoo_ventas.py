"""
odoo_ventas.py — La venta de un marketplace se vuelve ORDEN DE VENTA en Odoo.

POR QUÉ EXISTE
──────────────
Odoo ya recibe solas las ventas de Mercado Libre (5,214 órdenes `ML <id>` que
crea el módulo `meli_oerp`). Las de TikTok, Temu y Shein las captura A MANO
Gabriela: 216 + 49 + 420 órdenes, sin `client_order_ref` — es decir, sin
ninguna liga al marketplace y sin defensa contra duplicados. Este módulo hace
para TikTok/Temu lo que `meli_oerp` hace para ML.

LA DECISIÓN QUE MANDA: **ODOO DESCUENTA, WOO NO** (dale de Brandon, 27-ago-2026)
────────────────────────────────────────────────────────────────────────────────
Cada venta tiene que descontar UNA sola vez. Antes de este módulo descontaba
dos veces, y está medido en producción:

    26-ago 03:03–03:10  se capturan a mano 6 pzas de MUN-0023-MUL (TikTok)
    26-ago 03:18:42     stock_watch: "delta de Odoo (foto 103 -> 97)" → Woo 39 -> 33

…y esas ventas YA le habían bajado su pieza a Woo al volverse pedido. Del 21 al
26 de agosto, `stock_watch` le quitó a Woo **95 piezas de ese solo SKU** por
deltas de Odoo.

La cadena correcta, y la que este módulo asume, es una sola:

    venta → orden de venta en Odoo → (confirmar) reserva → free_qty baja
          → stock_watch → Woo → fan-out → los demás canales

Por eso el pedido de Woo de estos canales pasa a nacer PROTEGIDO
(`proteger_stock=True`), igual que ML FULL y Amazon FBA: el descuento real
llega por Odoo. El costo conocido de esta decisión es la LATENCIA — hasta 20
minutos entre la venta y el descuento en los canales, donde antes era
instantáneo.

EL ALMACÉN SE ELIGE POR STOCK, NO POR COSTUMBRE
───────────────────────────────────────────────
Gabriela suele usar TEXCO, pero la mercancía no siempre está ahí. Medido:

    VIA-0024-NEG   TEXCO: 0 libres   ·   TEXCO II: 30 físicas, 1 libre

Fijar TEXCO a secas haría nacer la orden en un almacén sin mercancía: Odoo la
confirma igual (NO bloquea por falta de stock — probado con 10,000 piezas), el
picking se queda en `confirmed` sin reservar nunca, y `free_qty` NO baja. O sea
que la venta no descontaría en ningún lado — que con la decisión de arriba
significa sobreventa silenciosa en todos los canales.

Así que el almacén se ELIGE: se lee `free_qty` con `context={"warehouse": id}`
y gana el primero que cubra la orden completa. Si ninguno la cubre, se toma el
que cubra más y **queda anotado como cobertura parcial** — nunca en silencio.

TODO ESTO ES SÍNCRONO Y BLOQUEA (regla 11)
──────────────────────────────────────────
XML-RPC es bloqueante. Cada función de aquí se llama desde `asyncio.to_thread`;
llamarlas dentro de una corrutina detiene el backend ENTERO mientras Odoo
contesta, no solo a quien llamó. Costó el apagón del 13-ago.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from config import settings
from services import odoo

log = logging.getLogger("omnicanal.odoo_ventas")

# El partner por canal. Son los MISMOS que ya usa Gabriela, a propósito: el
# almacén no tiene que aprender nada nuevo, y así el comprador real NUNCA entra
# a Odoo (TikTok marca `seller.order.info` como dato personal — meterlo aquí
# sería regar PII sin necesidad: quien envía es el marketplace, no nosotros).
_PARTNER = {"tiktok": 1739238, "temu": 1738206}

# Cómo se escribe el canal donde lo va a LEER una persona (`origin` y la nota de
# la orden). `canal.capitalize()` producía "Tiktok", que en la pantalla del
# almacén se ve como un error de dedo.
_ETIQUETA = {"tiktok": "TikTok", "temu": "Temu"}

# Orden de preferencia. DROP OFF (142) queda FUERA a propósito: es el almacén de
# drop-shipping, no mercancía nuestra que se pueda surtir.
_ALMACENES = [(135, "TEXCO"), (150, "TEXCO II")]


def _kw(modelo: str, metodo: str, args: list, kwargs: dict | None = None) -> Any:
    """Llamada cruda a Odoo reutilizando la sesión de `services.odoo`."""
    uid = odoo._uid()
    if not uid:
        raise RuntimeError("Odoo: no se pudo autenticar")
    return odoo._models().execute_kw(
        settings.odoo_db, uid, settings.odoo_password, modelo, metodo,
        args, kwargs or {})


# ── El interruptor ──────────────────────────────────────────────────────────
# El switch del panel manda; la variable de entorno es el valor por omisión.
# Ver el encabezado de `ops.automatizacion_flags` en la migración 0033 para el
# porqué (un apagado en memoria se deshace solo en el siguiente deploy).
#
# CACHÉ CON VENCIMIENTO, y no es optimización prematura: `habilitado()` se
# consulta en CADA venta —incluidas las ~3,700 semanales de Mercado Libre que
# ni siquiera van a Odoo—, y es una lectura a kubera con psycopg2, que BLOQUEA.
# Sin caché, cada venta pagaría un viaje a Postgres para preguntar algo que
# cambia una vez al mes.
_FLAG = "odoo_ventas_enabled"
_cache: dict[str, Any] = {"valor": None, "ts": 0.0, "por": None, "motivo": None}
_TTL = 30.0          # segundos: un apagado tarda a lo más medio minuto en surtir


def _leer_flag() -> dict[str, Any] | None:
    """El interruptor persistido, o None si nadie lo ha tocado / no hay tabla."""
    from services import supabase_db as sdb
    try:
        return sdb.fetch_one(
            "select valor, motivo, actualizado_por, actualizado_at "
            "from ops.automatizacion_flags where flag = %(f)s", {"f": _FLAG})
    except Exception as exc:  # noqa: BLE001
        # Sin tabla (migración 0033 sin aplicar) o kubera caída: se cae al valor
        # por omisión. Se avisa una vez cada TTL, no en cada venta.
        log.debug("odoo_ventas: no se pudo leer el interruptor (%s)", exc)
        return None


def habilitado(refrescar: bool = False) -> bool:
    """
    ¿Está encendida la generación de órdenes en Odoo?

    ⚠️ BLOQUEA cuando el caché vence: llamar desde un hilo (`asyncio.to_thread`),
    nunca dentro de una corrutina (regla 11).
    """
    ahora = time.time()
    if refrescar or _cache["valor"] is None or (ahora - _cache["ts"]) > _TTL:
        fila = _leer_flag()
        _cache.update(
            valor=(bool(fila["valor"]) if fila
                   else bool(getattr(settings, "odoo_ventas_enabled", False))),
            ts=ahora,
            por=(fila or {}).get("actualizado_por"),
            motivo=(fila or {}).get("motivo"),
            persistido=bool(fila))
    return bool(_cache["valor"])


# ── El interruptor POR CANAL ────────────────────────────────────────────────
# Cada canal tiene su propia fila en `ops.automatizacion_flags` con la llave
# `odoo_ventas_canal_<canal>`. No hizo falta migración: la tabla ya es
# (flag, valor), justo la forma que esto necesita.
#
# POR QUÉ POR CANAL Y NO UNA LISTA. TikTok y Temu no están en el mismo punto:
# TikTok lleva semanas de observación y Temu acaba de estrenar su webhook. Un
# solo interruptor obligaría a encenderlos juntos, y apagar Temu por un
# problema suyo se llevaría a TikTok por delante.
_cache_canales: dict[str, dict[str, Any]] = {}


def _flag_canal(canal: str) -> str:
    return f"{_FLAG}_canal_{canal}"


def canal_activo(canal: str, refrescar: bool = False) -> bool:
    """¿Está encendido ESE canal? ⚠️ BLOQUEA: llamar desde un hilo."""
    canal = (canal or "").lower()
    if canal not in _CANALES_POSIBLES:
        return False
    c = _cache_canales.setdefault(canal, {"valor": None, "ts": 0.0})
    ahora = time.time()
    if refrescar or c["valor"] is None or (ahora - c["ts"]) > _TTL:
        fila = None
        from services import supabase_db as sdb
        try:
            fila = sdb.fetch_one(
                "select valor, motivo, actualizado_por from ops.automatizacion_flags "
                "where flag = %(f)s", {"f": _flag_canal(canal)})
        except Exception as exc:  # noqa: BLE001
            log.debug("odoo_ventas: interruptor de %s no legible (%s)", canal, exc)
        if fila:
            c.update(valor=bool(fila["valor"]), persistido=True,
                     por=fila.get("actualizado_por"), motivo=fila.get("motivo"))
        else:
            # Sin fila: manda la variable de entorno, que es el valor por omisión.
            crudo = str(getattr(settings, "odoo_ventas_canales", "") or "")
            porom = {x.strip().lower() for x in crudo.split(",") if x.strip()}
            c.update(valor=canal in porom, persistido=False, por=None, motivo=None)
        c["ts"] = ahora
    return bool(c["valor"])


def fijar_canal(canal: str, encendido: bool, quien: str = "",
                motivo: str = "") -> dict[str, Any]:
    """Mueve el switch de UN canal. Nunca lanza."""
    from services import supabase_db as sdb
    canal = (canal or "").lower()
    if canal not in _CANALES_POSIBLES:
        return {"ok": False, "motivo": f"canal '{canal}' no soportado"}
    try:
        sdb.execute(
            """insert into ops.automatizacion_flags
                   (flag, valor, motivo, actualizado_at, actualizado_por)
               values (%(f)s, %(v)s, %(m)s, now(), %(q)s)
               on conflict (flag) do update set
                   valor = excluded.valor, motivo = excluded.motivo,
                   actualizado_at = now(), actualizado_por = excluded.actualizado_por""",
            {"f": _flag_canal(canal), "v": bool(encendido),
             "m": (motivo or "")[:300] or None, "q": (quien or "")[:120] or None})
        log.warning("Órdenes de venta en Odoo · canal %s: %s por %s%s", canal,
                    "ENCENDIDO" if encendido else "APAGADO", quien or "?",
                    f" — {motivo}" if motivo else "")
        return {"ok": True, **estado_interruptor()}
    except Exception as exc:  # noqa: BLE001
        log.exception("no se pudo mover el interruptor del canal %s", canal)
        return {**estado_interruptor(), "ok": False, "motivo": str(exc)[:300]}


def estado_interruptor() -> dict[str, Any]:
    """Lo que pinta el switch: el general, y el de cada canal."""
    habilitado(refrescar=True)
    canales_estado = {}
    for c in sorted(_CANALES_POSIBLES):
        canal_activo(c, refrescar=True)
        d = _cache_canales.get(c, {})
        canales_estado[c] = {
            "encendido": bool(d.get("valor")),
            "persistido": bool(d.get("persistido")),
            "actualizado_por": d.get("por"),
            "motivo": d.get("motivo"),
        }
    return {
        "encendido": bool(_cache["valor"]),
        "persistido": bool(_cache.get("persistido")),
        "por_omision": bool(getattr(settings, "odoo_ventas_enabled", False)),
        "actualizado_por": _cache.get("por"),
        "motivo": _cache.get("motivo"),
        "canales_estado": canales_estado,
    }


def fijar_interruptor(encendido: bool, quien: str = "",
                      motivo: str = "") -> dict[str, Any]:
    """Mueve el switch. Devuelve el estado resultante; nunca lanza."""
    from services import supabase_db as sdb
    try:
        sdb.execute(
            """insert into ops.automatizacion_flags
                   (flag, valor, motivo, actualizado_at, actualizado_por)
               values (%(f)s, %(v)s, %(m)s, now(), %(q)s)
               on conflict (flag) do update set
                   valor = excluded.valor, motivo = excluded.motivo,
                   actualizado_at = now(), actualizado_por = excluded.actualizado_por""",
            {"f": _FLAG, "v": bool(encendido), "m": (motivo or "")[:300] or None,
             "q": (quien or "")[:120] or None})
        log.warning("Órdenes de venta en Odoo: %s por %s%s",
                    "ENCENDIDAS" if encendido else "APAGADAS", quien or "?",
                    f" — {motivo}" if motivo else "")
        return {"ok": True, **estado_interruptor()}
    except Exception as exc:  # noqa: BLE001
        log.exception("no se pudo mover el interruptor de odoo_ventas")
        # El `**estado_interruptor()` va PRIMERO: ese dict trae su propia llave
        # `motivo` (el del apagado guardado) y, puesto después, se comía el
        # mensaje del error. El panel mostraba el fallo en blanco — que es la
        # peor forma de fallar en un botón de encendido.
        return {**estado_interruptor(), "ok": False, "motivo": str(exc)[:300]}


# Los canales que este módulo SABE atender. Es una constante y no una consulta
# a propósito: el seam la usa como filtro barato en CADA venta —incluidas las
# ~3,700 semanales de Mercado Libre, que nunca van a Odoo por aquí— y una
# lectura a kubera ahí dentro bloquearía la corrutina de la venta (regla 11).
# La decisión REAL por canal se toma dentro del hilo, en `crear_orden`.
_CANALES_POSIBLES = frozenset({"tiktok", "temu"})


def canales_posibles() -> frozenset[str]:
    return _CANALES_POSIBLES


def canales() -> set[str]:
    """Los canales encendidos HOY. El switch por canal manda; la variable de
    entorno es el valor por omisión.

    ⚠️ BLOQUEA: llamar desde un hilo, nunca dentro de una corrutina."""
    return {c for c in _CANALES_POSIBLES if canal_activo(c)}


# ── Resolución de producto ──────────────────────────────────────────────────

def productos_por_sku(skus: list[str]) -> dict[str, dict[str, Any]]:
    """{ sku: {id, name, list_price} } por `default_code`. Los que no existan
    simplemente no aparecen — el que llama DEBE notar la ausencia."""
    limpios = sorted({(s or "").strip() for s in skus if (s or "").strip()})
    if not limpios:
        return {}
    filas = _kw("product.product", "search_read",
                [[["default_code", "in", limpios]]],
                {"fields": ["default_code", "name", "list_price", "uom_id"]})
    return {(f["default_code"] or "").strip(): f for f in filas}


def libre_por_almacen(product_ids: list[int]) -> dict[int, dict[int, float]]:
    """
    { product_id: { warehouse_id: libres } }.

    `free_qty` con `context={"warehouse": id}` es lo que de verdad se puede
    prometer EN ESE ALMACÉN: ya le restó lo comprometido por otras órdenes.
    Se pide almacén por almacén porque el contexto es uno por lectura.
    """
    salida: dict[int, dict[int, float]] = {p: {} for p in product_ids}
    if not product_ids:
        return salida
    for wid, _nombre in _ALMACENES:
        filas = _kw("product.product", "read", [product_ids, ["free_qty"]],
                    {"context": {"warehouse": wid}})
        for f in filas:
            salida.setdefault(f["id"], {})[wid] = float(f.get("free_qty") or 0)
    return salida


def elegir_almacen(lineas: list[dict[str, Any]],
                   libres: dict[int, dict[int, float]]) -> dict[str, Any]:
    """
    Qué almacén surte esta orden. Gana el PRIMERO que la cubra completa.

    `lineas` = [{product_id, cantidad, sku}]. Devuelve el almacén, cómo quedó la
    cobertura y la FOTO del stock por SKU — esa foto es lo que se guarda como
    "stock al momento de la venta" y es irrepetible: dentro de 20 minutos ya no
    se puede reconstruir.
    """
    # LA FOTO SE LLAVEA POR ID DE ALMACÉN, NO POR NOMBRE. El nombre es una
    # etiqueta que alguien puede editar en Odoo cualquier martes; el id no. Con
    # llaves por nombre, renombrar "TEXCO II" hacía que la foto se guardara en
    # NULL **en silencio** — y esa foto es justo el dato irrecuperable.
    foto: dict[str, dict[str, float]] = {}
    for ln in lineas:
        por_alm = libres.get(ln["product_id"], {})
        foto[ln["sku"]] = {str(wid): por_alm.get(wid, 0.0) for wid, _n in _ALMACENES}

    mejor: dict[str, Any] | None = None
    for wid, nombre in _ALMACENES:
        cubiertas = sum(1 for ln in lineas
                        if libres.get(ln["product_id"], {}).get(wid, 0) >= ln["cantidad"])
        piezas = sum(min(libres.get(ln["product_id"], {}).get(wid, 0), ln["cantidad"])
                     for ln in lineas)
        completo = cubiertas == len(lineas)
        cand = {"almacen_id": wid, "almacen": nombre, "completo": completo,
                "lineas_cubiertas": cubiertas, "piezas_cubiertas": piezas}
        if completo:
            return {**cand, "cobertura": "completa", "stock_foto": foto}
        if mejor is None or piezas > mejor["piezas_cubiertas"]:
            mejor = cand

    # Ninguno cubre todo. Se toma el que más cubra, pero SE DICE: una orden que
    # nace sin respaldo no se reserva, y con "Odoo descuenta" eso es sobreventa
    # esperando a ocurrir. Que salga en la pantalla es el punto.
    return {**(mejor or {"almacen_id": _ALMACENES[0][0], "almacen": _ALMACENES[0][1],
                         "lineas_cubiertas": 0, "piezas_cubiertas": 0}),
            "cobertura": "parcial", "stock_foto": foto}


# ── Idempotencia ────────────────────────────────────────────────────────────

def buscar_por_ref(canal: str, order_id: str) -> dict[str, Any] | None:
    """
    ¿Ya existe la orden de esta venta? Se busca por `client_order_ref` Y por
    partner: el ref solo es único DE HECHO (Odoo no le pone restricción), así
    que acotar por el partner del canal cierra cualquier choque entre ids de
    marketplaces distintos.
    """
    partner = _PARTNER.get(canal)
    dominio: list = [["client_order_ref", "=", str(order_id)]]
    if partner:
        dominio.append(["partner_id", "=", partner])
    filas = _kw("sale.order", "search_read", [dominio],
                {"fields": ["name", "state", "amount_total", "warehouse_id"],
                 "limit": 1})
    return filas[0] if filas else None


# ── Crear / cancelar ────────────────────────────────────────────────────────

def crear_orden(canal: str, order_id: str, fecha: str | None,
                items: list[dict[str, Any]],
                confirmar: bool | None = None,
                dry_run: bool = False) -> dict[str, Any]:
    """
    La orden de venta en Odoo. Idempotente por `client_order_ref`.

    `items` = [{sku, cantidad, precio_unitario, titulo}] — lo que ya trae
    normalizado `pedidos_tiktok`/`pedidos_temu`.

    `dry_run=True` calcula TODO —producto, almacén, foto de stock— y no escribe,
    sin importar en qué escalón estén las banderas. Es lo que hace seguro al
    endpoint `/simular`: sin este parámetro, "simular" dejaría de simular en
    cuanto alguien apagara `SOLO_REGISTRO`, y el que lo llamara para mirar
    estaría creando órdenes de verdad.

    NUNCA lanza: la llama el camino de una venta, y una venta no se puede caer
    porque Odoo no contestó. Un pedido sin orden en Odoo se repara; una venta
    perdida, no.
    """
    # EL INTERRUPTOR SE LEE AQUÍ, ya dentro del hilo, no en el seam: es una
    # consulta a kubera que BLOQUEA, y el seam corre en la corrutina de la venta
    # (regla 11). `dry_run` lo salta a propósito — simular tiene que funcionar
    # con la automatización apagada, que es justo cuando se quiere simular.
    if not dry_run and not habilitado():
        return {"ok": False, "accion": "apagado", "canal": canal,
                "order_id": order_id,
                "motivo": "el interruptor de órdenes en Odoo está apagado"}
    # El canal se decide AQUÍ, dentro del hilo, por lo mismo que el interruptor
    # general: es una lectura a kubera y bloquea. El seam solo pre-filtra con la
    # constante `_CANALES_POSIBLES`, que no toca la base.
    if not dry_run and not canal_activo(canal):
        return {"ok": False, "accion": "canal_apagado", "canal": canal,
                "order_id": order_id,
                "motivo": f"el canal {canal} está apagado en Automatización"}
    if confirmar is None:
        confirmar = bool(getattr(settings, "odoo_ventas_confirmar", False))
    solo_registro = dry_run or bool(getattr(settings, "odoo_ventas_solo_registro", True))
    partner = _PARTNER.get(canal)
    if not partner:
        return {"ok": False, "motivo": f"canal '{canal}' sin partner configurado"}

    try:
        # 1 · Idempotencia ANTES de nada.
        previa = buscar_por_ref(canal, order_id)
        if previa:
            return {"ok": True, "accion": "ya_existia", "odoo_id": previa["id"],
                    "nombre": previa["name"], "estado": previa["state"],
                    "almacen": (previa.get("warehouse_id") or [None, None])[1]}

        # 2 · Los SKUs, todos o ninguno. Una orden a medias hace que el almacén
        #     surta incompleto sin enterarse — peor que no tener orden.
        pedidos_sku = [(i.get("sku") or "").strip() for i in items]
        prods = productos_por_sku(pedidos_sku)
        faltan = [s for s in pedidos_sku if s and s not in prods]
        if faltan:
            return {"ok": False, "accion": "sku_sin_producto", "skus_faltantes": faltan,
                    "motivo": f"sin producto en Odoo: {', '.join(faltan)}"}
        if not prods:
            return {"ok": False, "motivo": "la venta no trae ningún SKU legible"}

        lineas = [{"product_id": prods[(i["sku"] or "").strip()]["id"],
                   "sku": (i["sku"] or "").strip(),
                   "cantidad": int(i.get("cantidad") or 1),
                   "precio": float(i.get("precio_unitario") or 0),
                   "titulo": i.get("titulo") or ""}
                  for i in items if (i.get("sku") or "").strip()]

        # 3 · El almacén, por stock. Y la foto irrepetible del inventario.
        libres = libre_por_almacen([l["product_id"] for l in lineas])
        alm = elegir_almacen(lineas, libres)

        # 4 · El payload. Se arma ANTES de decidir si se escribe, a propósito:
        #     así el modo observación puede DEVOLVERLO y se puede revisar
        #     exactamente lo que se le mandaría a Odoo, campo por campo, sin
        #     escribir nada. Un "simulador" que no enseña el payload obliga a
        #     confiar en que el código hace lo que dice.
        vals = {
            "partner_id": partner,
            "warehouse_id": alm["almacen_id"],
            "client_order_ref": str(order_id),      # ← la llave de idempotencia
            "origin": f"{_ETIQUETA.get(canal, canal)} {order_id}",
            "note": (f"Creada automáticamente desde {_ETIQUETA.get(canal, canal)} "
                     f"(orden {order_id}). Panel Omnicanal."),
            "order_line": [(0, 0, {
                "product_id": l["product_id"],
                "product_uom_qty": l["cantidad"],
                "price_unit": l["precio"],
                "name": (f"[{l['sku']}] {l['titulo']}"[:400] or l["sku"]),
            }) for l in lineas],
        }
        # La fecha de la VENTA, no la de captura: si no, la contabilidad y
        # cualquier reporte por día quedan corridos (mismo error que deformó el
        # tab de Ventas con un backfill fechado "hoy").
        if fecha:
            try:
                vals["date_order"] = (datetime.fromisoformat(str(fecha))
                                      .astimezone(timezone.utc)
                                      .strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:  # noqa: BLE001
                pass

        if solo_registro:
            # Modo observación: se calculó TODO —producto, almacén, foto de
            # stock, payload— y no se escribe. Es lo que permite comparar contra
            # las capturas de Gabriela sin riesgo.
            return {"ok": True, "accion": "simulado" if dry_run else "solo_registro",
                    "canal": canal,
                    "order_id": order_id, "almacen": alm["almacen"],
                    "almacen_id": alm["almacen_id"], "cobertura": alm["cobertura"],
                    "stock_foto": alm["stock_foto"], "payload": vals,
                    "lineas": [{"sku": l["sku"], "cantidad": l["cantidad"],
                                "precio": l["precio"]} for l in lineas]}

        oid = _kw("sale.order", "create", [vals])
        creada = _kw("sale.order", "read", [[oid], ["name", "state", "amount_total"]])[0]
        accion = "creada"

        # 5 · Confirmar (aquí es donde Odoo RESERVA y `free_qty` baja).
        if confirmar:
            _kw("sale.order", "action_confirm", [[oid]])
            creada = _kw("sale.order", "read",
                         [[oid], ["name", "state", "amount_total"]])[0]
            accion = "confirmada"

        log.info("Odoo %s: orden %s %s (venta %s, almacén %s, cobertura %s)",
                 canal, creada["name"], accion, order_id,
                 alm["almacen"], alm["cobertura"])
        return {"ok": True, "accion": accion, "odoo_id": oid,
                "nombre": creada["name"], "estado": creada["state"],
                "total": creada["amount_total"], "canal": canal,
                "order_id": order_id, "almacen": alm["almacen"],
                "almacen_id": alm["almacen_id"], "cobertura": alm["cobertura"],
                "stock_foto": alm["stock_foto"],
                "lineas": [{"sku": l["sku"], "cantidad": l["cantidad"],
                            "precio": l["precio"]} for l in lineas]}
    except Exception as exc:  # noqa: BLE001 — jamás rompe la venta
        log.exception("odoo_ventas.crear_orden(%s, %s) falló", canal, order_id)
        return {"ok": False, "motivo": str(exc)[:300], "canal": canal,
                "order_id": order_id}


def cancelar_orden(canal: str, order_id: str) -> dict[str, Any]:
    """
    El marketplace canceló: la orden de Odoo se cancela también.

    No es cosmético. De las ventas de TikTok que se revisaron, la MAYORÍA
    terminó cancelada; una orden viva por una venta muerta deja al almacén
    surtiendo lo que nadie compró y —con "Odoo descuenta"— deja la reserva
    mordiendo stock que sí se podía vender.
    """
    if not habilitado():
        return {"ok": False, "accion": "apagado",
                "motivo": "el interruptor de órdenes en Odoo está apagado"}
    try:
        previa = buscar_por_ref(canal, order_id)
        if not previa:
            return {"ok": False, "accion": "sin_orden",
                    "motivo": "no hay orden en Odoo para esa venta"}
        if previa["state"] == "cancel":
            return {"ok": True, "accion": "ya_cancelada", "odoo_id": previa["id"],
                    "nombre": previa["name"]}
        if bool(getattr(settings, "odoo_ventas_solo_registro", True)):
            return {"ok": True, "accion": "solo_registro_cancelar",
                    "odoo_id": previa["id"], "nombre": previa["name"]}
        # ⚠️ `action_cancel` NO SIEMPRE CANCELA, y contesta igual (auditoría
        # 28-ago). Este Odoo es 17.0+e, y ahí una orden confirmada con entrega
        # en `done` o factura publicada NO se cancela: el método devuelve la
        # ACCIÓN del asistente `sale.order.cancel` —que existe en esta base— y
        # queda esperando a un humano. La versión anterior devolvía
        # "cancelada" sin volver a leer nada, así que reportaba como hecho algo
        # que no había pasado: el peor modo de fallo posible para una
        # cancelación, porque nadie va a ir a revisarla.
        #
        # `disable_cancel_warning` salta el asistente cuando se puede; y el
        # estado se RELEE siempre, que es lo único que de verdad lo prueba
        # (`crear_orden` ya releía tras `action_confirm`; esto faltaba).
        _kw("sale.order", "action_cancel", [[previa["id"]]],
            {"context": {"disable_cancel_warning": True}})
        despues = _kw("sale.order", "read", [[previa["id"]], ["state", "name"]])[0]
        if despues["state"] != "cancel":
            log.warning("Odoo %s: orden %s NO se canceló (quedó en '%s') — venta %s",
                        canal, previa["name"], despues["state"], order_id)
            return {"ok": False, "accion": "no_se_pudo_cancelar",
                    "odoo_id": previa["id"], "nombre": previa["name"],
                    "estado": despues["state"],
                    "motivo": f"Odoo la dejó en '{despues['state']}': "
                              "probablemente tiene entrega hecha o factura "
                              "publicada y hay que cancelarla a mano"}
        log.info("Odoo %s: orden %s CANCELADA (venta %s)",
                 canal, previa["name"], order_id)
        return {"ok": True, "accion": "cancelada", "odoo_id": previa["id"],
                "nombre": previa["name"], "estado": "cancel"}
    except Exception as exc:  # noqa: BLE001
        log.exception("odoo_ventas.cancelar_orden(%s, %s) falló", canal, order_id)
        return {"ok": False, "motivo": str(exc)[:300]}
