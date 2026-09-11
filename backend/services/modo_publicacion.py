"""
modo_publicacion.py — cómo sale al canal una familia de variantes.

QUÉ RESUELVE
------------
Un producto con variantes se puede publicar de dos formas, y la elección NO es
una preferencia visual: cambia qué se crea en el marketplace y qué se puede
editar.

  · agrupada    una publicación con selector de variante. Título, descripción,
                categoría e imágenes se comparten; sólo precio, stock, GTIN e
                imagen principal son de cada variante.
  · individual  una publicación por variante, cada una con su ficha completa.

ES POR CANAL, NO GLOBAL. Mercado Libre y Amazon admiten variantes nativas;
TikTok las maneja distinto. El mismo producto puede estar agrupado en ML y
separado en TikTok sin contradicción.

LA LLAVE ES EL SKU DEL PADRE. El modo es de la FAMILIA: decir "agrupada" sobre
una sola hija no significa nada. El padre se resuelve por `wp_posts.post_parent`
(`wp_db.sku_padre`), NUNCA por el prefijo del SKU — tomar los dos primeros
segmentos ya fusionó 104 pares en WooCommerce, de los que 34 eran productos
distintos (ver `services/odoo.py:640`).

⚠️ AGRUPADA TODAVÍA NO SE PUEDE EJECUTAR EN MERCADO LIBRE (10-sep-2026).
El publicador arma un payload plano: la llave `variations` no aparece en el
repositorio, y hay 0 publicaciones con variantes en las dos cuentas. El panel
la ofrece como «Pronto» y este módulo REHÚSA guardarla mientras
`AGRUPADA_HABILITADA` esté vacío. Guardar un modo que nadie sabe ejecutar sería
prometer en la base algo que el publicador no cumple.

QUÉ **NO** ES
-------------
  · NO decide si una familia PUEDE agruparse (eso lo dice la categoría del
    canal: `tags.allow_variations` en ML, hoy sin leer).
  · NO es el estado de la publicación  → channel.listings
  · NO es el contenido por canal       → enrich.channel_content

DEGRADADO
---------
Sin `SUPABASE_DB_URL` (típico en un `.env` local) o con la migración 0051 sin
aplicar, `disponible()` es False y todo contesta 'individual' — que es lo que
el panel hace HOY. Nunca revienta la pantalla por no poder leer una preferencia.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from services import channel_content

log = logging.getLogger("omnicanal.modo_publicacion")

TABLA = "channel.publication_mode"

MODOS = ("agrupada", "individual")
POR_OMISION = "individual"

# Canales donde el panel SABE ejecutar el modo agrupado. Vacío a propósito: el
# día que el adaptador aprenda a mandar `variations`, se agrega 'mercado_libre'
# aquí y el modo deja de ser «Pronto» sin tocar nada más.
AGRUPADA_HABILITADA: tuple[str, ...] = ()

# La 0051 puede no estar aplicada todavía. Se avisa UNA vez y se sigue con el
# valor por omisión: una preferencia que no se puede leer no es motivo para
# tumbar el Estudio.
_AVISADO_SIN_TABLA = False


def disponible() -> bool:
    return channel_content.disponible()


def _sin_tabla(exc: Exception) -> bool:
    """psycopg2.errors.UndefinedTable sin importar psycopg2 aquí."""
    return getattr(exc, "pgcode", None) == "42P01"


def _avisar_sin_tabla() -> None:
    global _AVISADO_SIN_TABLA
    if not _AVISADO_SIN_TABLA:
        _AVISADO_SIN_TABLA = True
        log.warning(
            "%s no existe (migración 0051 sin aplicar): el Estudio usa '%s' "
            "para todos los canales", TABLA, POR_OMISION)


def _motivo_fk(texto: str, sku: str, canal: str) -> str | None:
    """El motivo legible de las dos llaves foráneas de la 0051.

    Misma disciplina que `channel_content.guardar`: el nombre de la
    restricción viene en el texto del error, y lo que se pinta en pantalla
    tiene que entenderse sin saber Postgres.
    """
    if "publication_mode_sku_fkey" in texto:
        return (f"El SKU {sku} todavía no está en el maestro (core.products). "
                "Lo agrega el ETL de las 06:15 UTC.")
    if "publication_mode_canal_fkey" in texto:
        return f"El canal '{canal}' no existe en core.channels."
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Lectura
# ══════════════════════════════════════════════════════════════════════════════

def _leer_sync(skus: list[str]) -> dict[str, dict[str, str]]:
    cx = channel_content.pool_kubera().connection()
    try:
        with cx.cursor() as cur:
            cur.execute(
                f"select sku::text, canal, modo from {TABLA} where sku = any(%s::citext[])",
                (skus,),
            )
            filas = cur.fetchall()
    finally:
        cx.close()
    out: dict[str, dict[str, str]] = {}
    for sku, canal, modo in filas:
        out.setdefault(sku, {})[canal] = modo
    return out


async def leer(skus: list[str]) -> dict[str, dict[str, str]]:
    """{ sku_padre: {canal: modo} } — sólo los que tienen algo guardado."""
    skus = [s for s in dict.fromkeys(skus) if s]
    if not skus or not disponible():
        return {}
    try:
        return await asyncio.to_thread(_leer_sync, skus)
    except Exception as exc:  # noqa: BLE001
        if _sin_tabla(exc):
            _avisar_sin_tabla()
        else:
            log.warning("modo_publicacion.leer falló: %s", exc)
        return {}


async def leer_uno(sku: str) -> dict[str, str]:
    """{ canal: modo } de una familia. Los canales sin fila NO aparecen."""
    return (await leer([sku])).get(sku, {})


# ══════════════════════════════════════════════════════════════════════════════
# Escritura
# ══════════════════════════════════════════════════════════════════════════════

def _guardar_sync(sku: str, canal: str, modo: str, quien: str | None) -> None:
    cx = channel_content.pool_kubera().connection()
    try:
        with cx.cursor() as cur:
            cur.execute(
                f"""insert into {TABLA} (sku, canal, modo, updated_by)
                    values (%s, %s, %s, %s)
                    on conflict (sku, canal) do update
                       set modo = excluded.modo,
                           updated_at = now(),
                           updated_by = excluded.updated_by""",
                (sku, canal, modo, quien),
            )
        cx.commit()
    finally:
        cx.close()


async def guardar(sku: str, canal: str, modo: str,
                  quien: str | None = None) -> dict[str, Any]:
    """
    Guarda el modo de una familia en un canal.

    Devuelve `{"guardado": bool, "modo": str, "motivo": str|None}` en vez de
    reventar: el panel tiene que poder seguir aunque la preferencia no se pueda
    persistir, y el motivo es lo que se pinta en pantalla.
    """
    if modo not in MODOS:
        return {"guardado": False, "modo": POR_OMISION,
                "motivo": f"modo desconocido: {modo!r}"}
    if modo == "agrupada" and canal not in AGRUPADA_HABILITADA:
        # No es una validación de formulario: es la verdad del publicador. Ver
        # el aviso del encabezado.
        return {"guardado": False, "modo": POR_OMISION,
                "motivo": "El modo agrupado todavía no se puede publicar en "
                          "este canal — el publicador manda una ficha plana."}
    if not disponible():
        return {"guardado": False, "modo": modo,
                "motivo": "Sin conexión a kubera: el modo no se guardó."}
    try:
        await asyncio.to_thread(_guardar_sync, sku, canal, modo, quien)
        return {"guardado": True, "modo": modo, "motivo": None}
    except Exception as exc:  # noqa: BLE001
        if _sin_tabla(exc):
            _avisar_sin_tabla()
            return {"guardado": False, "modo": modo,
                    "motivo": "Falta aplicar la migración 0051."}
        motivo = _motivo_fk(str(exc), sku, canal)
        if motivo:
            return {"guardado": False, "modo": modo, "motivo": motivo}
        log.warning("modo_publicacion.guardar falló: %s", exc)
        return {"guardado": False, "modo": modo, "motivo": str(exc)[:160]}
