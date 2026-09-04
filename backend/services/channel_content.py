"""
channel_content.py — el contenido editorial POR CANAL: leer y guardar.

QUÉ RESUELVE
------------
El Estudio ya sabe GENERAR contenido por canal (`ia_generadores.GENERADORES`:
6 tipos para Amazon, 3 para ML, 1 para TikTok) y no sabía guardarlo:
`POST /api/ia/generar` devolvía el texto y ahí moría. El único botón de guardar
(`POST /api/productos/{sku}/contenido`) escribe a WooCommerce y NO recibe canal.

Resultado medido: si no publicabas en la misma sesión, lo generado se perdía.

Este módulo es la otra mitad: `enrich.channel_content`, llave
`(sku, canal, cuenta)`, un documento jsonb por canal.

QUÉ **NO** ES
-------------
  · NO es la bitácora de envíos      → ops.channel_submissions
  · NO es el estado de la publicación → channel.listings
  · NO es el catálogo de requisitos   → channel_requirements (aún no existe)

LAS LLAVES DEL DOCUMENTO SON CANÓNICAS
--------------------------------------
Se guarda `titulo`, `descripcion`, `bullets`, `highlights`, `atributos` — los
mismos nombres que ya usa el panel (`routers/publicar.py::CamposReq`). La
traducción a `item_name` / `productName` / `goodsName` vive en el publicador,
no aquí. Así, cuando un marketplace renombre un campo, se toca un adaptador y
no se migran datos.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.channel_content")

# Centinela de "este SKU no tiene categoría en el canal". Era un NUL literal, y
# psycopg2 lo RECHAZA en un parámetro: la consulta reventaba con "A string
# literal cannot contain NUL (0x00) characters" y el semáforo se apagaba entero.
# Salió al abrir Temu en el Estudio, donde el resolvedor todavía no tenía rama y
# devolvía None. Sirve cualquier cadena que no pueda ser un categoria_id real;
# el NUL no, aunque parezca el "vacío perfecto".
_SIN_CATEGORIA = "__sin_categoria__"

# Centinela de "este SKU no tiene categoría en el canal". Era un NUL literal, y psycopg2
# RECHAZA el NUL en un parámetro: la consulta reventaba con "A string literal
# cannot contain NUL (0x00) characters" y el semáforo se apagaba entero. Salió
# al abrir Temu en el Estudio, donde el resolvedor todavía no tenía rama y
# devolvía None. Cualquier cadena que no pueda ser un categoria_id real sirve;
# el NUL no, aunque parezca el "vacío perfecto".
_SIN_CATEGORIA = "__sin_categoria__"

TABLA = "enrich.channel_content"

# Canales válidos: son los ids de core.channels y hay una FK que lo verifica.
# 'meli' NO existe — la FK lo rechaza.
CANALES = ("general", "mercado_libre", "amazon", "tiktok", "walmart", "temu", "shein")


def _dsn() -> str:
    """
    La BD kubera. `supabase_db_url` PRIMERO y `kubera_db_url` de respaldo.

    POR QUÉ ESE ORDEN (y no al revés, que fue el primer intento): en producción
    las dos apuntan a la misma base, pero `env.staging` define SOLO
    `SUPABASE_DB_URL`. Pedir `kubera_db_url` dejaba este módulo muerto en
    staging — el guardado respondía "KUBERA_DB_URL no configurada" mientras el
    resto del panel funcionaba contra el sandbox. Detectado al probar el panel
    local contra el sandbox.
    """
    return settings.supabase_db_url or settings.kubera_db_url


def disponible() -> bool:
    return bool(_dsn())


_POOL = None
_POOL_LOCK = threading.Lock()


def _pool():
    """
    Pool propio, chico y perezoso.

    El primer intento reusaba el de `kubera_mirror` para no duplicar el
    presupuesto de conexiones (el suyo está acotado a 6 porque el 23-jul se
    perdieron 60 eventos por `TooManyConnections`). Pero ese pool se construye
    sobre `kubera_db_url`, que es justo la variable que falta en staging — así
    que el reuso ataba este módulo a un ambiente.

    Éste abre como mucho 3 conexiones, `mincached=0` (no abre ninguna hasta que
    alguien guarda) y `blocking=False` (pool lleno = error registrado, no una
    espera que cuelgue al panel). El uso es interactivo —una persona dándole a
    Guardar—, no ráfagas.
    """
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                import psycopg2
                from dbutils.pooled_db import PooledDB
                _POOL = PooledDB(
                    creator=psycopg2, maxconnections=3, mincached=0,
                    maxcached=2, blocking=False, ping=1,
                    dsn=_dsn(), connect_timeout=4,
                )
    return _POOL


# ══════════════════════════════════════════════════════════════════════════════
# Lectura
# ══════════════════════════════════════════════════════════════════════════════

def _leer_sync(sku: str, canal: str, cuenta: str) -> dict[str, Any] | None:
    cx = _pool().connection()
    try:
        with cx.cursor() as cur:
            cur.execute(
                f"""select categoria, contenido, origen, spec_version, hash_base,
                           updated_at
                      from {TABLA}
                     where sku = %s and canal = %s and cuenta = %s""",
                (sku, canal, cuenta),
            )
            fila = cur.fetchone()
    finally:
        cx.close()
    if not fila:
        return None
    return {
        "sku": sku, "canal": canal, "cuenta": cuenta,
        "categoria": fila[0], "contenido": fila[1] or {}, "origen": fila[2] or {},
        "spec_version": fila[3], "hash_base": fila[4],
        "updated_at": fila[5].isoformat() if fila[5] else None,
    }


async def leer(sku: str, canal: str, cuenta: str = "") -> dict[str, Any] | None:
    """El documento guardado de un SKU en un canal. None si no hay nada."""
    if not disponible():
        return None
    try:
        return await asyncio.to_thread(_leer_sync, sku, canal, cuenta)
    except Exception as exc:  # noqa: BLE001
        log.warning("channel_content.leer(%s,%s) falló: %s", sku, canal, exc)
        return None


def _resumen_sync(sku: str) -> list[dict[str, Any]]:
    cx = _pool().connection()
    try:
        with cx.cursor() as cur:
            cur.execute(
                f"""select canal, cuenta, categoria,
                           (select count(*) from jsonb_object_keys(contenido)),
                           updated_at
                      from {TABLA}
                     where sku = %s
                     order by canal, cuenta""",
                (sku,),
            )
            filas = cur.fetchall()
    finally:
        cx.close()
    return [{"canal": f[0], "cuenta": f[1], "categoria": f[2], "campos": f[3],
             "updated_at": f[4].isoformat() if f[4] else None}
            for f in filas]


async def resumen(sku: str) -> list[dict[str, Any]]:
    """Qué canales tienen contenido guardado y cuántos campos. Para pintar las
    pestañas del Estudio sin traerse los documentos completos."""
    if not disponible():
        return []
    try:
        return await asyncio.to_thread(_resumen_sync, sku)
    except Exception as exc:  # noqa: BLE001
        log.warning("channel_content.resumen(%s) falló: %s", sku, exc)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# El semáforo: qué le falta a un SKU para publicarse en un canal
# ══════════════════════════════════════════════════════════════════════════════

def _faltantes_sync(sku: str, canal: str, cuenta: str,
                    categoria: str | None,
                    datos: dict[str, Any] | None = None) -> dict[str, Any]:
    cx = _pool().connection()
    try:
        with cx.cursor() as cur:
            # ¿Hay requisitos leídos para este canal+categoría? Si no, el panel
            # NO debe decir "está completo" — debe decir "no sabemos". Solo 12
            # de los 558 productTypes de Amazon están cargados.
            cur.execute(
                """select count(*), max(leido_at)
                     from channel.field_requirements
                    where canal = %s and categoria_id in ('*', %s)""",
                (canal, categoria or _SIN_CATEGORIA),
            )
            cuantos, leido_at = cur.fetchone()
            if not cuantos:
                return {"estado": "sin_requisitos", "faltan": [], "automaticos": [],
                        "categoria": categoria, "leido_at": None}

            # DOS FORMAS DE COMPROBAR, según dónde viva el campo:
            #
            #   campo_canonico = 'atributos'  → se mira DENTRO de la lista, por
            #     el `nombre` de cada entrada. Es el caso de Mercado Libre: sus
            #     obligatorios por categoría (BRAND, MODEL…) son atributos de la
            #     ficha, no campos de primer nivel.
            #   cualquier otro canónico       → basta la presencia de la llave.
            #   sin canónico                  → nadie puede llenarlo: siempre falta.
            #
            # POR QUÉ NO ALCANZABA `contenido ? campo_canonico`: si los atributos
            # de ML se mapearan a `atributos` a secas, el semáforo se pondría
            # VERDE en cuanto el producto tuviera cualquier atributo, aunque le
            # faltara justo el obligatorio. Un falso verde es peor que no saber
            # — es lo mismo que evita la tercera luz del semáforo.
            #
            # Un atributo presente pero con valor VACÍO cuenta como ausente.
            #
            # La precedencia se resuelve ANTES de filtrar por `obligatorio`: si
            # se filtra primero, la fila de la categoría específica que dice
            # `obligatorio=false` desaparece y gana la de '*'. Medido.
            cur.execute(
                """with efectivo as (
                     select distinct on (campo) *
                       from channel.field_requirements
                      where canal = %s and categoria_id in ('*', %s)
                      order by campo, (categoria_id <> '*') desc
                   ),
                   cont as (
                     select coalesce(
                       (select contenido from enrich.channel_content
                         where sku = %s and canal = %s and cuenta = %s),
                       '{}'::jsonb) as j
                   )
                   select e.campo, e.campo_canonico, e.default_value, f.label
                     from efectivo e
                     cross join cont
                     left join core.canonical_fields f on f.campo = e.campo_canonico
                    where e.obligatorio
                      and not (case
                        when e.campo_canonico = 'atributos' and e.campo <> 'atributos'
                          then exists (
                            select 1 from jsonb_array_elements(
                                     coalesce(cont.j -> 'atributos', '[]'::jsonb)) a
                             -- Se acepta por `campo` O por `nombre`. `campo` es
                             -- el nombre NATIVO del canal, que es el que trae
                             -- `field_requirements` (`product_attributes.100107`
                             -- en TikTok, `BRAND` en ML); `nombre` es el legible
                             -- que ve una persona. Comparar solo contra `nombre`
                             -- daba falso ROJO en cuanto el generador guardaba el
                             -- atributo correcto con su etiqueta humana.
                             where (a ->> 'campo' = e.campo or a ->> 'nombre' = e.campo)
                               and coalesce(a ->> 'valor', '') <> '')
                        when e.campo_canonico is not null
                          then cont.j ? e.campo_canonico
                        else false
                      end)
                    order by e.campo""",
                (canal, categoria or _SIN_CATEGORIA, sku, canal, cuenta),
            )
            filas = cur.fetchall()
    finally:
        cx.close()

    # LO QUE EL PRODUCTO YA TIENE FUERA DEL DOCUMENTO.
    #
    # `enrich.channel_content` guarda lo EDITORIAL (título, descripción,
    # bullets, atributos). Las imágenes, el precio, el stock, las dimensiones y
    # la categoría viven en WooCommerce y en el propio canal, y el publicador
    # los toma de ahí — así que exigirlos en el documento pintaba en rojo cosas
    # que sí están.
    #
    # Medido el 13-ago: `MUN-0023-MUL`, PUBLICADO Y A LA VENTA en TikTok, salía
    # con 8 obligatorios "faltantes" — categoría, imágenes, peso, medidas, stock
    # y precio. Los seis existían. Un semáforo que marca en rojo lo que sí está
    # enseña a ignorarlo, que es peor que no tenerlo.
    tiene = {k for k, v in (datos or {}).items()
             if v not in (None, "", [], {}, 0)}
    if categoria:
        tiene.add("categoria_id")   # se resolvió arriba: por definición, está

    # ── WALMART: SUS OBLIGATORIOS NO SE COMPRUEBAN COMO LOS DE NADIE ─────
    #
    # Aquí `campo_canonico` es NULL en todas sus filas, y la consulta de arriba
    # trata "sin canónico" como "nadie puede llenarlo: siempre falta". Resultado
    # medido en JUGU-0164-ROS: **30 obligatorios en rojo con el contenido ya
    # generado**, incluidos los 9 que el propio generador acababa de reportar
    # como 9/9 cubiertos. Dos pantallas de la misma app diciéndose lo contrario.
    #
    # Walmart guarda sus atributos como OBJETO (`{"gender": "Niña", ...}`), no
    # como la lista `[{campo, valor}]` de ML y TikTok, así que se comprueba por
    # CLAVE. Y sus obligatorios se parten en dos familias:
    #   · los del bloque `Visible` de ESA categoría → los llena la IA o una
    #     persona, y ahí sí tiene sentido pedirlos.
    #   · todo lo demás (brand, price, sku, ShippingWeight, ProductTaxCode,
    #     sellerWarranty…) → lo arma `_item()` del publicador con datos de Woo.
    #     Pedirlos en el documento pinta en rojo cosas que SÍ van en el feed — la
    #     misma leccción del 13-ago con MUN-0023-MUL en TikTok.
    wm_visibles: set[str] = set()
    wm_obligatorios: list[str] = []
    wm_atrs: set[str] = set()
    if canal == "walmart":
        # La lista de obligatorios sale de `walmart_contenido.catalogo()`, NO de
        # la consulta de arriba, y esa diferencia importa: `catalogo()` aplica
        # las CORRECCIONES_MEDIDAS encima de la tabla. Leyendo la tabla cruda,
        # el semáforo no pedía `activity` en "Ropa" — el campo exacto que tumbó
        # el piloto del 4-sep— y hubiera dicho "Listo para Walmart" sobre un
        # producto que Walmart iba a rechazar. Un verde falso es peor que un
        # rojo falso.
        try:
            from services import walmart_contenido as _wc
            _cat = _wc.catalogo(categoria or "")
            wm_visibles = set(_cat["campos"])
            wm_obligatorios = [c for c in _cat["obligatorios"]
                               if c not in _cat["rechazados"]]
        except Exception as exc:  # noqa: BLE001 — sin catálogo, se cae al crudo
            log.warning("faltantes(walmart,%s): sin catálogo (%s); se usa la "
                        "tabla cruda", categoria, exc)
            wm_obligatorios = [f[0] for f in filas]
            wm_visibles = set(wm_obligatorios)
        cx2 = _pool().connection()
        try:
            with cx2.cursor() as cur2:
                cur2.execute(
                    """select contenido -> 'atributos' from enrich.channel_content
                        where sku = %s and canal = %s and cuenta = %s""",
                    (sku, canal, cuenta))
                fila_a = cur2.fetchone()
                crudo = fila_a[0] if fila_a else None
                if isinstance(crudo, dict):
                    wm_atrs = {k for k, v in crudo.items()
                               if v not in (None, "", [], {})}
                elif isinstance(crudo, list):   # por si alguien guarda la otra forma
                    wm_atrs = {str(a.get("campo") or a.get("nombre") or "")
                               for a in crudo if isinstance(a, dict)}
        finally:
            cx2.close()

    faltan, automaticos, cubiertos_fuera = [], [], []

    if canal == "walmart":
        etiquetas = {f[0]: (f[3] or f[0]) for f in filas}
        for campo in sorted(wm_obligatorios):
            if campo in wm_atrs:
                cubiertos_fuera.append({"campo": campo, "canonico": "atributos"})
            else:
                faltan.append({"campo": campo, "canonico": None,
                               "label": etiquetas.get(campo, campo)})
        # Todo obligatorio que NO sea del bloque `Visible` de esta categoría lo
        # arma `_item()` del publicador con datos de Woo (brand, price, sku,
        # ShippingWeight, ProductTaxCode, sellerWarranty…). Pedirlos en el
        # documento pinta en rojo cosas que SÍ van en el feed — la misma
        # lección del 13-ago con MUN-0023-MUL en TikTok.
        for campo, _canonico, _default, _label in filas:
            if campo not in wm_visibles:
                automaticos.append({"campo": campo,
                                    "valor": "lo arma el publicador"})
        return {
            "estado": "ok" if not faltan else "incompleto",
            "faltan": faltan, "automaticos": automaticos,
            "del_producto": cubiertos_fuera,
            "categoria": categoria,
            "leido_at": leido_at.isoformat() if leido_at else None,
        }

    for campo, canonico, default, label in filas:
        if canonico and canonico in tiene:
            cubiertos_fuera.append({"campo": campo, "canonico": canonico})
        elif default is not None:
            automaticos.append({"campo": campo, "valor": default})
        else:
            faltan.append({"campo": campo, "canonico": canonico,
                           "label": label or canonico or campo})
    return {
        "estado": "ok" if not faltan else "incompleto",
        "faltan": faltan, "automaticos": automaticos,
        # Lo que se cubre con datos del producto (Woo/canal) y no con el
        # documento: se devuelve para poder explicarlo, no para esconderlo.
        "del_producto": cubiertos_fuera,
        "categoria": categoria,
        "leido_at": leido_at.isoformat() if leido_at else None,
    }


async def faltantes(sku: str, canal: str, cuenta: str = "",
                    categoria: str | None = None,
                    datos: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Qué le falta a un SKU para publicarse en un canal.

    Tres estados, no dos — es lo que evita pintar en rojo lo que se llena solo:
      · `faltan`      → nadie los llena. Rojo de verdad.
      · `automaticos` → los pone el publicador con su respaldo.
      · `sin_requisitos` → NO hay requisitos leídos para esa categoría. El panel
        debe decir "sin verificar", NUNCA "está completo": de los 558
        productTypes de Amazon solo hay 12 cargados.
    """
    if not disponible():
        return {"estado": "sin_requisitos", "faltan": [], "automaticos": [],
                "categoria": categoria, "leido_at": None}
    try:
        return await asyncio.to_thread(_faltantes_sync, sku, canal, cuenta,
                                       categoria, datos)
    except Exception as exc:  # noqa: BLE001
        log.warning("channel_content.faltantes(%s,%s) falló: %s", sku, canal, exc)
        return {"estado": "sin_requisitos", "faltan": [], "automaticos": [],
                "categoria": categoria, "leido_at": None}


# ══════════════════════════════════════════════════════════════════════════════
# Los requisitos crudos de una categoría — para ALIMENTAR a la IA, no para pintar
# ══════════════════════════════════════════════════════════════════════════════

def _requisitos_sync(canal: str, categoria: str,
                     solo_obligatorios: bool) -> list[dict[str, Any]]:
    cx = _pool().connection()
    try:
        with cx.cursor() as cur:
            # Misma precedencia que `faltantes`: la fila de la categoría gana a
            # la de '*', y se resuelve ANTES de filtrar por `obligatorio` — si se
            # filtra primero, la fila específica que dice `obligatorio=false`
            # desaparece y gana la del comodín. Medido.
            cur.execute(
                """with efectivo as (
                     select distinct on (campo) *
                       from channel.field_requirements
                      where canal = %s and categoria_id in ('*', %s)
                      order by campo, (categoria_id <> '*') desc
                   )
                   select campo, campo_canonico, obligatorio, tipo,
                          valores_permitidos, default_value
                     from efectivo
                    where (%s = false or obligatorio)
                    order by obligatorio desc, campo""",
                (canal, categoria, solo_obligatorios),
            )
            filas = cur.fetchall()
    finally:
        cx.close()
    return [{"campo": f[0], "campo_canonico": f[1], "obligatorio": f[2],
             "tipo": f[3], "valores_permitidos": f[4], "default_value": f[5]}
            for f in filas]


async def requisitos(canal: str, categoria: str | None,
                     solo_obligatorios: bool = True) -> list[dict[str, Any]]:
    """
    Qué exige el canal para esa categoría, en crudo.

    `faltantes()` contesta "¿qué le falta a este SKU?"; ésta contesta "¿qué pide
    esta categoría?" sin mirar producto — que es lo que necesita el generador de
    IA para pedirle a la IA justo esos campos y no los que se imagine.

    ⚠️ EL CRUCE ES `listings.product_type = field_requirements.categoria_id`.
    `listings.category_id` existe y guarda OTRA cosa: unir por ahí devuelve cero
    filas sin dar error, y el semáforo diría "sin_requisitos" en todo el catálogo
    teniendo 64,125 requisitos cargados.
    """
    if not disponible() or not categoria:
        return []
    try:
        return await asyncio.to_thread(_requisitos_sync, canal, categoria,
                                       solo_obligatorios)
    except Exception as exc:  # noqa: BLE001
        log.warning("channel_content.requisitos(%s,%s) falló: %s", canal, categoria, exc)
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Escritura
# ══════════════════════════════════════════════════════════════════════════════

def _guardar_sync(sku: str, canal: str, cuenta: str, contenido: dict,
                  origen: dict | None, categoria: str | None,
                  spec_version: str | None, hash_base: str | None,
                  reemplazar: bool) -> dict[str, Any]:
    # MERGE por omisión (`||` de jsonb, superficial): guardar solo
    # {"highlights": "..."} NO debe borrar los bullets que ya estaban. El panel
    # manda una pestaña a la vez, así que reemplazar sería destructivo.
    #
    # reemplazar=True existe para el único caso donde hace falta: BORRAR un
    # campo. Con merge no hay forma de quitar una llave.
    expr_contenido = ("excluded.contenido" if reemplazar
                      else f"{TABLA}.contenido || excluded.contenido")
    expr_origen = ("excluded.origen" if reemplazar
                   else f"coalesce({TABLA}.origen,'{{}}'::jsonb) "
                        f"|| coalesce(excluded.origen,'{{}}'::jsonb)")

    cx = _pool().connection()
    try:
        with cx.cursor() as cur:
            cur.execute(
                f"""insert into {TABLA}
                      (sku, canal, cuenta, account_id, categoria, contenido,
                       origen, spec_version, hash_base, updated_at)
                    values (%s, %s, %s,
                            (select id from core.accounts where legacy_code = %s),
                            %s, %s::jsonb, %s::jsonb, %s, %s, now())
                    on conflict (sku, canal, cuenta) do update set
                      categoria    = coalesce(excluded.categoria, {TABLA}.categoria),
                      contenido    = {expr_contenido},
                      origen       = {expr_origen},
                      spec_version = coalesce(excluded.spec_version, {TABLA}.spec_version),
                      hash_base    = coalesce(excluded.hash_base, {TABLA}.hash_base),
                      updated_at   = now()
                    returning (select count(*) from jsonb_object_keys(contenido))""",
                (sku, canal, cuenta, cuenta or None, categoria,
                 json.dumps(contenido, ensure_ascii=False),
                 json.dumps(origen or {}, ensure_ascii=False),
                 spec_version, hash_base),
            )
            campos = (cur.fetchone() or [0])[0]
        cx.commit()
    finally:
        cx.close()
    return {"ok": True, "sku": sku, "canal": canal, "cuenta": cuenta,
            "campos": campos}


async def guardar(sku: str, canal: str, contenido: dict[str, Any], *,
                  cuenta: str = "", origen: dict[str, Any] | None = None,
                  categoria: str | None = None, spec_version: str | None = None,
                  hash_base: str | None = None,
                  reemplazar: bool = False) -> dict[str, Any]:
    """
    Guarda (o fusiona) el contenido de un SKU en un canal.

    Por omisión FUSIONA: mandar solo {"highlights": "..."} conserva lo demás.
    `reemplazar=True` pisa el documento entero — el único modo de borrar campos.

    Nunca lanza: devuelve {"ok": False, "motivo": …} para que el panel muestre
    algo legible en vez de un 500.
    """
    if not disponible():
        return {"ok": False, "motivo": "KUBERA_DB_URL no configurada."}
    if canal not in CANALES:
        return {"ok": False,
                "motivo": f"Canal '{canal}' inválido. Válidos: {', '.join(CANALES)}."}
    if not isinstance(contenido, dict) or not contenido:
        return {"ok": False, "motivo": "El contenido viene vacío."}

    try:
        return await asyncio.to_thread(
            _guardar_sync, sku, canal, cuenta, contenido, origen, categoria,
            spec_version, hash_base, reemplazar,
        )
    except Exception as exc:  # noqa: BLE001
        texto = str(exc)
        # Se detecta por el NOMBRE de la constraint, no por el del esquema.
        # Postgres reporta `table "products"` a secas, sin el `core.`, así que
        # buscar "core" en el mensaje no encontraba nada y el 409 salía con el
        # error crudo de la base. Visto al probar el panel contra el sandbox.
        if "channel_content_sku_fkey" in texto:
            log.warning("channel_content.guardar(%s): SKU fuera de core.products", sku)
            return {"ok": False, "sku": sku,
                    "motivo": f"El SKU {sku} todavía no está en el maestro "
                              f"(core.products). Lo agrega el ETL de las 06:15 UTC."}
        if "channel_content_account_id_fkey" in texto:
            return {"ok": False, "sku": sku,
                    "motivo": f"La cuenta '{cuenta}' no existe en core.accounts."}
        if "channel_content_canal_fkey" in texto:
            return {"ok": False, "sku": sku,
                    "motivo": f"El canal '{canal}' no existe en core.channels."}
        log.warning("channel_content.guardar(%s,%s) falló: %s", sku, canal, exc)
        return {"ok": False, "sku": sku, "motivo": texto[:300]}
