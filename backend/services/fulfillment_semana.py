"""
fulfillment_semana.py — La SEMANA de FULL: su plan y su chat con la IA (Brandon,
28-sep-2026: "la planeación es week over week… al iniciar una nueva week la
planeación estará vacía… el chat deberá de respetar el tiempo week over week para
el reset: al inicio de la nueva semana se reseteará y se creará un chat de la week").

Una semana es la ISO: de lunes a domingo, en hora de CDMX («2026-S40», del 28 sep al
4 oct). Cada una tiene UN plan y UN chat, compartidos por todo el equipo. Cuando empieza
la siguiente, su plan y su chat nacen vacíos; los de las anteriores quedan de consulta
(el selector de semana de FULLFILMENT). Sólo la semana EN CURSO se escribe.

DÓNDE VIVEN: en la bitácora de acciones (`ops.process_log`), como la solicitud de Crear
FULL desde v0.567.0 — sin tabla nueva. Proceso 'fulfillment_semana', SIN SKU (es el
estado de una pantalla, no un paso de un producto: así no aparece como «último paso» de
ningún SKU en Inventario). `detail_ref` = «fulfillment_semana:<semana>:<acción>:…».

    'plan'   la foto COMPLETA del plan: qué va de cada SKU a cada tienda, quién lo puso
             (la IA, a mano, la propuesta estándar) y por qué. Cada guardado es una fila
             y manda la última: así queda el historial de cómo se armó la semana.
    'datos'  la planeación que se le dio a la IA, una por día. DeepSeek relee de su
             caché los prefijos que se repiten: si la conversación se rearma con la
             MISMA planeación y los mismos textos, cada turno de seguimiento sólo paga
             lo nuevo.
    'turno'  un turno del chat: la instrucción, el mensaje EXACTO que se mandó, la
             respuesta EXACTA de la IA y lo que el panel validó.

Nada de esto toca Odoo ni ningún marketplace. Todo BLOQUEA (psycopg2): el router lo
corre en un hilo (regla 11).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from services import supabase_db as sdb

log = logging.getLogger("omnicanal.fulfillment_semana")

PROCESO = "fulfillment_semana"
_CDMX = timezone(timedelta(hours=-6))       # México ya no cambia de horario (2022)
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
_RE_CLAVE = re.compile(r"^(\d{4})-S(\d{1,2})$")

TIENDAS_VALIDAS = ("meli:Kubera", "meli:San Corpe", "amazon", "walmart")
ORIGENES = ("ia", "manual", "estandar")
MAX_ENTRADAS = 4000


# ── La semana (funciones puras) ─────────────────────────────────────────────

def rango(lunes: date) -> str:
    """«28 sep – 4 oct» o «14–20 sep»."""
    fin = lunes + timedelta(days=6)
    mi, mf = _MESES[lunes.month - 1], _MESES[fin.month - 1]
    return f"{lunes.day}–{fin.day} {mf}" if mi == mf else f"{lunes.day} {mi} – {fin.day} {mf}"


def _info(anio: int, numero: int, lunes: date) -> dict[str, Any]:
    return {"clave": f"{anio}-S{numero}", "semana": f"S{numero}", "anio": anio, "numero": numero,
            "lunes": lunes.isoformat(), "domingo": (lunes + timedelta(days=6)).isoformat(),
            "rango": rango(lunes)}


def semana_de(cuando: datetime | date | None = None) -> dict[str, Any]:
    """La semana ISO de un instante, en hora de CDMX (un domingo a las 23:00 es de su semana)."""
    if cuando is None:
        cuando = datetime.now(timezone.utc)
    if isinstance(cuando, datetime):
        dia = (cuando if cuando.tzinfo else cuando.replace(tzinfo=timezone.utc)).astimezone(_CDMX).date()
    else:
        dia = cuando
    anio, numero, _ = dia.isocalendar()
    return _info(anio, numero, date.fromisocalendar(anio, numero, 1))


def semana_por_clave(clave: str | None) -> dict[str, Any] | None:
    """«2026-S40» → la semana. None si la clave no es una semana."""
    m = _RE_CLAVE.match((clave or "").strip())
    if not m:
        return None
    anio, numero = int(m.group(1)), int(m.group(2))
    try:
        return _info(anio, numero, date.fromisocalendar(anio, numero, 1))
    except ValueError:
        return None


def dia_cdmx(cuando: datetime | None = None) -> str:
    """El día de hoy en CDMX («2026-09-28»)."""
    cuando = cuando or datetime.now(timezone.utc)
    return cuando.astimezone(_CDMX).date().isoformat()


def en_semana(iso: str | None, clave: str) -> bool:
    """¿Ese instante (ISO con zona) cae en esa semana, en hora de CDMX?"""
    if not iso:
        return False
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return False
    return semana_de(dt)["clave"] == clave


# ── El plan (función pura) ──────────────────────────────────────────────────

def _entero(v: Any, tope: int = 100_000) -> int:
    try:
        return max(0, min(tope, int(v)))
    except (TypeError, ValueError):
        return 0


def _texto(v: Any, largo: int) -> str | None:
    t = str(v).strip() if v is not None else ""
    return t[:largo] or None


def limpiar_plan(plan: Any) -> dict[str, Any]:
    """
    El plan como lo guarda la pantalla, sin basura: tiendas conocidas, cantidades
    enteras, un renglón por tienda y SKU (manda el último) y textos recortados.
    """
    plan = plan if isinstance(plan, dict) else {}
    entradas: dict[tuple[str, str], dict[str, Any]] = {}
    for e in (plan.get("entradas") or [])[:MAX_ENTRADAS]:
        if not isinstance(e, dict):
            continue
        tienda, sku = str(e.get("tienda") or ""), (_texto(e.get("sku"), 60) or "")
        if tienda not in TIENDAS_VALIDAS or not sku:
            continue
        origen = e.get("origen") if e.get("origen") in ORIGENES else "manual"
        entradas[(tienda, sku)] = {
            "tienda": tienda, "sku": sku, "cantidad": _entero(e.get("cantidad")), "origen": origen,
            "motivo": _texto(e.get("motivo"), 300), "turno": _texto(e.get("turno"), 24),
            "reemplazo_de": _texto(e.get("reemplazo_de"), 60), "incluido": e.get("incluido") is not False,
        }
    quitados = sorted({str(q)[:80] for q in (plan.get("quitados") or [])[:MAX_ENTRADAS]
                       if isinstance(q, str) and "|" in q and q.split("|", 1)[0] in TIENDAS_VALIDAS})
    activas = [t for t in TIENDAS_VALIDAS if t in (plan.get("activas") or [])]
    parametros = {k: _entero(v, 100_000) for k, v in (plan.get("parametros") or {}).items()
                  if k in ("cobertura_dias", "ventana_dias", "min_piezas", "min_ventas", "dejar_en_bodega")}
    return {"version": _entero(plan.get("version"), 10**9), "entradas": list(entradas.values()),
            "quitados": quitados, "activas": activas, "parametros": parametros}


def resumen_plan(plan: dict[str, Any] | None) -> dict[str, Any]:
    """Cuánto va en un plan guardado, por tienda: SKUs, piezas y reemplazos."""
    por: dict[str, dict[str, int]] = {}
    for e in (plan or {}).get("entradas") or []:
        if not e.get("incluido", True) or not e.get("cantidad"):
            continue
        t = por.setdefault(e["tienda"], {"skus": 0, "piezas": 0, "reemplazos": 0, "de_ia": 0})
        t["skus"] += 1
        t["piezas"] += int(e["cantidad"])
        t["reemplazos"] += 1 if e.get("reemplazo_de") else 0
        t["de_ia"] += 1 if e.get("origen") == "ia" else 0
    total = {k: sum(v[k] for v in por.values()) for k in ("skus", "piezas", "reemplazos", "de_ia")}
    return {"por_tienda": por, **total}


# ── La bitácora (BLOQUEA) ───────────────────────────────────────────────────

def _ref(clave: str, accion: str, sufijo: str = "") -> str:
    return f"{PROCESO}:{clave}:{accion}" + (f":{sufijo}" if sufijo else "")


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else (str(v) if v else None)


def guardar(clave: str, accion: str, detalle: dict[str, Any], quien: str = "", sufijo: str = "") -> int | None:
    """Una fila nueva en la bitácora de la semana. Devuelve su id."""
    fila = sdb.execute_returning(
        """insert into ops.process_log (proceso, origen, accion, estado, detalle, detail_ref, actor)
           values (%(p)s, 'panel', %(a)s, 'ok', %(d)s::jsonb, %(r)s, %(q)s) returning id""",
        {"p": PROCESO, "a": accion, "d": json.dumps(detalle, ensure_ascii=False, default=str),
         "r": _ref(clave, accion, sufijo), "q": (quien or "")[:120] or None})
    return int(fila["id"]) if fila else None


# Lo que ve la pantalla: sin la planeación completa ni los textos de la conversación
# (son para la IA y pesan cientos de KB).
_SQL_SEMANA = """
    select id, accion, actor, created_at,
           case when accion = 'datos' then detalle - 'datos'
                when accion = 'turno' then detalle - 'mensaje' - 'salida'
                else detalle end as detalle
      from ops.process_log
     where proceso = %(p)s and detail_ref like %(pref)s and accion in ('plan', 'turno', 'datos')
     order by id
"""


def leer(clave: str) -> dict[str, Any]:
    """El plan (el último guardado), los turnos del chat y cuándo se le dio la planeación a la IA."""
    turnos: list[dict[str, Any]] = []
    plan = datos = None
    for f in sdb.fetch_all(_SQL_SEMANA, {"p": PROCESO, "pref": f"{PROCESO}:{clave}:%"}):
        d = f.get("detalle") or {}
        if f["accion"] == "turno":
            turnos.append({**d, "creado": _iso(f["created_at"]), "quien": f.get("actor")})
        elif f["accion"] == "plan":
            plan = {**d, "id": f["id"], "guardado": _iso(f["created_at"]), "quien": f.get("actor")}
        else:
            datos = {**d, "id": f["id"], "creado": _iso(f["created_at"]), "quien": f.get("actor")}
    # Un turno es una fila; si alguno se repitiera (un reintento), manda el último.
    ultimos: dict[str, dict[str, Any]] = {}
    for t in turnos:
        ultimos[str(t.get("id") or len(ultimos))] = t
    return {"turnos": list(ultimos.values()), "plan": plan, "datos": datos}


def conversacion(clave: str) -> dict[str, Any]:
    """
    Lo que necesita la IA para seguir el chat de la semana: la ÚLTIMA planeación que
    se le dio y los turnos terminados con sus textos exactos, en orden.
    """
    datos = sdb.fetch_one(
        """select id, detalle from ops.process_log
            where proceso = %(p)s and detail_ref like %(pref)s and accion = 'datos'
            order by id desc limit 1""",
        {"p": PROCESO, "pref": f"{PROCESO}:{clave}:%"})
    # Ojo: el id del TURNO va como «turno». Con alias «id», el `order by id` ordenaría por
    # ese texto (el alias le gana a la columna) y la conversación saldría revuelta.
    filas = sdb.fetch_all(
        """select l.id as fila, l.detalle->>'id' as turno, l.detalle->>'mensaje' as mensaje,
                  l.detalle->>'salida' as salida
             from ops.process_log l
            where l.proceso = %(p)s and l.detail_ref like %(pref)s and l.accion = 'turno'
              and l.detalle->>'estado' = 'listo'
            order by l.id""",
        {"p": PROCESO, "pref": f"{PROCESO}:{clave}:%"})
    turnos = [{"id": f["turno"], "mensaje": f["mensaje"], "salida": f["salida"]}
              for f in filas if f.get("mensaje") and f.get("salida")]
    return {"datos": ({**(datos["detalle"] or {}), "id": datos["id"]} if datos else None), "turnos": turnos}


def guardar_plan(clave: str, plan: Any, quien: str = "") -> dict[str, Any]:
    limpio = limpiar_plan(plan)
    rid = guardar(clave, "plan", limpio, quien, sufijo=str(limpio["version"]))
    log.info("FULL %s: plan v%s guardado por %s (%s renglones)", clave, limpio["version"],
             (quien or "?").split("@")[0], len(limpio["entradas"]))
    return {"ok": True, "id": rid, "version": limpio["version"], "renglones": len(limpio["entradas"])}
