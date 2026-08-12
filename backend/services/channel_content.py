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
from typing import Any

from config import settings

log = logging.getLogger("omnicanal.channel_content")

TABLA = "enrich.channel_content"

# Canales válidos: son los ids de core.channels y hay una FK que lo verifica.
# 'meli' NO existe — la FK lo rechaza.
CANALES = ("general", "mercado_libre", "amazon", "tiktok", "walmart", "temu", "shein")


def disponible() -> bool:
    return bool(settings.kubera_db_url)


def _pool():
    """
    Se REUSA el pool de kubera_mirror en vez de abrir uno propio.

    Es la misma base, y ese pool ya está acotado a 6 conexiones a propósito:
    el 23-jul se perdieron 60 eventos por `TooManyConnections` con un pool de 3
    (ver el comentario de kubera_mirror._get_pool). Un segundo pool duplicaría
    el presupuesto contra la misma instancia y reabriría ese problema.
    """
    from services import kubera_mirror
    return kubera_mirror._get_pool()  # noqa: SLF001 — deliberado, ver arriba


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
        # La FK a core.products: el maestro se llena con el cron
        # `etl-core-products` de las 06:15 UTC, así que un SKU nacido hoy en Woo
        # puede no estar todavía. Se reporta claro en vez de fallar en silencio.
        if "core" in texto and ("foreign key" in texto.lower() or "fkey" in texto):
            log.warning("channel_content.guardar(%s): SKU fuera de core.products", sku)
            return {"ok": False, "sku": sku,
                    "motivo": f"El SKU {sku} todavía no está en el maestro "
                              f"(core.products). Lo agrega el ETL de las 06:15 UTC."}
        log.warning("channel_content.guardar(%s,%s) falló: %s", sku, canal, exc)
        return {"ok": False, "sku": sku, "motivo": texto[:300]}
