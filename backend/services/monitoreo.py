"""
monitoreo.py — Cuánto lleva hecho cada persona, y en qué canal.

Lee `ops.process_log` filtrando por los procesos de PERSONA (publicar, precio,
stock). Deja fuera lo automático: el sondeo, el fan-out y los ETL también
escriben ahí, y contarlos inflaría los números con trabajo que nadie hizo.

⚠️ EL MISMO HUMANO PUEDE TENER DOS CORREOS. Thalía entra con `thalias@` o con
`sancorpethalia@` según la cuenta, así que sus movimientos aparecen partidos.
Se unifican aquí con `_MISMA_PERSONA` en vez de en la consulta: la bitácora debe
conservar el correo REAL con el que se hizo cada cosa —eso es lo que la vuelve
auditable— y la fusión es una decisión de presentación.
"""
from __future__ import annotations

import logging
from typing import Any

from services import supabase_db

log = logging.getLogger("omnicanal.monitoreo")

# Procesos que nacen de un botón. Ver services/bitacora.py.
_DE_PERSONA = ("publicar", "costos", "crear", "precio", "stock")

# ⚠️ `crear` escribe un renglon POR PASO, no por producto: "En cola…",
# "1/5 Scrapeando Alibaba…", "2/5 Mejorando titulo…". Medido el 1-sep: 147 filas
# de cola y 129 de scrapeo para un pu&#241;ado de productos. Contarlas todas infla
# el trabajo de cada persona ~10x y vuelve el tablero inservible.
#
# Solo cuentan los estados TERMINALES: el producto quedo creado, o no.
_INTERMEDIOS = ("en_cola", "procesando")

# Cada proceso llama distinto al exito. `crear` dice 'completado', los demas
# 'ok'. Sin esto, las creaciones saldrian todas como fallidas.
_EXITO = ("ok", "completado", "succeeded")

# Dos correos, una persona (Brandon, 5-ago). Se fusionan al MOSTRAR.
_MISMA_PERSONA = {"sancorpethalia@kubera.mx": "thalias@kubera.mx"}


def _persona(correo: str | None) -> str:
    c = (correo or "").strip().lower()
    return _MISMA_PERSONA.get(c, c)


def resumen(dias: int = 30) -> dict[str, Any]:
    """
    Por usuario y por canal: cuántas acciones, cuántas salieron bien, y cuándo
    fue la última.

    `exitos` y `total` van por separado a propósito. Un usuario con 40 intentos
    y 12 éxitos no es lo mismo que uno con 12 de 12, y esa diferencia es
    justamente lo que hay que ver — mide productividad Y señala dónde algo
    está rebotando.
    """
    try:
        filas = supabase_db.fetch_all(
            """select actor, proceso,
                      coalesce(detalle->>'canal', '(sin canal)') canal,
                      coalesce(detalle->>'cuenta', '') cuenta,
                      count(*) total,
                      count(*) filter (where estado = any(%s)) exitos,
                      max(created_at) ultima
                 from ops.process_log
                where proceso = any(%s)
                  and actor is not null
                  and estado <> all(%s)
                  and created_at >= now() - make_interval(days => %s)
                group by actor, proceso, canal, cuenta
                order by total desc""",
            (list(_EXITO), list(_DE_PERSONA), list(_INTERMEDIOS), dias),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudo leer el monitoreo: %s", exc)
        return {"ok": False, "motivo": str(exc)[:200], "usuarios": []}

    # Se agrupa en Python y no en SQL porque la fusión de los dos correos de
    # Thalía tiene que ocurrir DESPUÉS de leer, para no perder cuál se usó.
    por_usuario: dict[str, dict[str, Any]] = {}
    for f in filas:
        u = por_usuario.setdefault(_persona(f["actor"]), {
            "usuario": _persona(f["actor"]), "total": 0, "exitos": 0,
            "ultima": None, "correos": set(), "canales": {}, "procesos": {}})
        u["total"] += f["total"]
        u["exitos"] += f["exitos"]
        u["correos"].add(f["actor"])
        if u["ultima"] is None or f["ultima"] > u["ultima"]:
            u["ultima"] = f["ultima"]
        etiqueta = f"{f['canal']}·{f['cuenta']}" if f["cuenta"] else f["canal"]
        c = u["canales"].setdefault(etiqueta, {"total": 0, "exitos": 0})
        c["total"] += f["total"]; c["exitos"] += f["exitos"]
        p = u["procesos"].setdefault(f["proceso"], {"total": 0, "exitos": 0})
        p["total"] += f["total"]; p["exitos"] += f["exitos"]

    usuarios = sorted(por_usuario.values(), key=lambda x: -x["total"])
    for u in usuarios:
        u["correos"] = sorted(u["correos"])
        u["ultima"] = u["ultima"].isoformat() if u["ultima"] else None
    return {"ok": True, "dias": dias, "usuarios": usuarios,
            "total": sum(u["total"] for u in usuarios)}


def movimientos(limite: int = 100, usuario: str | None = None,
                canal: str | None = None, dias: int = 30) -> list[dict[str, Any]]:
    """El detalle, uno por uno: quién, qué, sobre qué SKU y cuándo."""
    where = ["proceso = any(%s)", "actor is not null",
             "estado <> all(%s)",
             "created_at >= now() - make_interval(days => %s)"]
    params: list[Any] = [list(_DE_PERSONA), list(_INTERMEDIOS), dias]
    if usuario:
        # Se busca por los DOS correos si es alguien con cuenta doble.
        correos = [usuario] + [c for c, u in _MISMA_PERSONA.items() if u == usuario]
        where.append("actor = any(%s)"); params.append(correos)
    if canal:
        where.append("detalle->>'canal' = %s"); params.append(canal)
    params.append(limite)
    try:
        return supabase_db.fetch_all(
            f"""select created_at, actor, proceso, accion, sku, estado,
                       detalle->>'canal' canal, detalle->>'cuenta' cuenta,
                       detalle, duracion_s
                  from ops.process_log
                 where {' and '.join(where)}
                 order by created_at desc limit %s""", tuple(params))
    except Exception as exc:  # noqa: BLE001
        log.warning("no se pudieron leer los movimientos: %s", exc)
        return []
