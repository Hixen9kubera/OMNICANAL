"""
migracion.py — Endpoints del panel /migracion (espejo kubera en tiempo real).

Sirven la interfaz que muestra el avance del dual-write hacia la BD
centralizada "kubera" a nivel archivo.py → tabla (services/kubera_mirror.py):

  GET  /api/migracion/estado            censo + contadores + flags
  GET  /api/migracion/eventos           ring buffer (últimos 500 intentos)
  GET  /api/migracion/errores           errores agrupados (plan de limpieza)
  GET  /api/migracion/deltas            camino al corte: actas + racha 14 días
  POST /api/migracion/errores/resolver  marca un grupo como resuelto

Todo es de LECTURA salvo el POST, que solo marca filas de la bitácora local
`espejo_kubera_log` (MySQL) como resueltas — lleva la API key del piloto.
"""
from __future__ import annotations

from datetime import timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.seguridad import requiere_api_key
from services import kubera_mirror

router = APIRouter(prefix="/api/migracion", tags=["migracion"])


@router.get("/estado")
def estado():
    return kubera_mirror.estado()


@router.get("/eventos")
def eventos(limit: int = 100):
    return {"eventos": kubera_mirror.eventos(limit)}


@router.get("/errores")
def errores(incluir_resueltos: bool = False):
    return {"grupos": kubera_mirror.errores_agrupados(incluir_resueltos)}


# Etiquetas humanas de los dominios auditados por los crons de deltas
_DOMINIOS_DELTAS = {
    "costing-deltas": "Costos",
    "channel-deltas": "Channel",
}
OBJETIVO_RACHA = 14  # regla de corte de la migración: 14 actas seguidas en cero


@router.get("/deltas")
def deltas(dias: int = 45):
    """Camino al corte: lee las actas de migration.reconciliation_runs (BD
    kubera) y calcula, por dominio, la racha de días consecutivos con delta=0.

    Solo lectura, best-effort: si la BD kubera no está configurada o falla la
    consulta, devuelve disponible=false y la página lo muestra sin romperse.
    Un día cuenta como "en cero" si su ÚLTIMA acta del día salió resultado='ok'
    (una re-corrida que corrige un delta el mismo día conserva el día).
    """
    from services import supabase_db as sdb  # import tardío: opcional en local

    if not sdb.disponible():
        return {"disponible": False, "objetivo": OBJETIVO_RACHA, "dominios": []}
    try:
        filas = sdb.fetch_all(
            "select dominio, resultado, conteos, created_at "
            "from migration.reconciliation_runs "
            "where dominio = any(%(dominios)s) "
            "  and created_at >= now() - make_interval(days => %(dias)s) "
            "order by created_at asc",
            {"dominios": list(_DOMINIOS_DELTAS), "dias": max(OBJETIVO_RACHA, min(dias, 120))},
        )
    except Exception as exc:  # noqa: BLE001 — la vista es informativa
        return {"disponible": False, "error": str(exc)[:200],
                "objetivo": OBJETIVO_RACHA, "dominios": []}

    dominios = []
    for dom, etiqueta in _DOMINIOS_DELTAS.items():
        actas = [f for f in filas if f["dominio"] == dom]
        # última acta de cada día (UTC) manda
        por_dia: dict = {}
        for f in actas:
            ts = f["created_at"]
            dia = ts.astimezone(timezone.utc).date()
            por_dia[dia] = f  # asc: la última del día queda
        historial = [
            {"fecha": d.isoformat(), "resultado": por_dia[d]["resultado"]}
            for d in sorted(por_dia)
        ]
        # racha: días CONSECUTIVOS en 'ok' terminando en el día más reciente
        racha = 0
        if por_dia:
            dia = max(por_dia)
            while dia in por_dia and por_dia[dia]["resultado"] == "ok":
                racha += 1
                dia = dia - timedelta(days=1)
        ultima = actas[-1] if actas else None
        dominios.append({
            "dominio": dom,
            "etiqueta": etiqueta,
            "racha": racha,
            "objetivo": OBJETIVO_RACHA,
            "ultima": None if ultima is None else {
                "ts": ultima["created_at"].isoformat(),
                "resultado": ultima["resultado"],
                "conteos": ultima["conteos"],
            },
            "historial": historial[-OBJETIVO_RACHA:],
        })
    return {"disponible": True, "objetivo": OBJETIVO_RACHA, "dominios": dominios}


class ResolverGrupo(BaseModel):
    archivo_py: str
    tabla_origen: str
    error_tipo: str


@router.post("/errores/resolver", dependencies=[Depends(requiere_api_key)])
def resolver(grupo: ResolverGrupo):
    n = kubera_mirror.resolver_grupo(
        grupo.archivo_py, grupo.tabla_origen, grupo.error_tipo)
    return {"resueltos": n}


@router.post("/errores/reprocesar", dependencies=[Depends(requiere_api_key)])
def reprocesar(max_items: int = 500):
    """Re-aplica los errores pendientes desde su payload_json (idempotente) y
    los marca resuelto=1. A diferencia de /errores/resolver, este SÍ escribe
    los datos perdidos en kubera antes de marcar."""
    return kubera_mirror.reprocesar_errores(max_items)
