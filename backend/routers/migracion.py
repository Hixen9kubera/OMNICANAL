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
    est = kubera_mirror.estado()
    # F5: contadores kubera-vs-fallback de las lecturas por dominio (el testigo
    # que demuestra que un flag encendido de verdad lee de kubera)
    from services import lecturas_fuente
    est["lecturas"] = lecturas_fuente.estado()
    return est


@router.get("/eventos")
def eventos(limit: int = 100):
    return {"eventos": kubera_mirror.eventos(limit)}


@router.get("/errores")
def errores(incluir_resueltos: bool = False):
    return {"grupos": kubera_mirror.errores_agrupados(incluir_resueltos)}


# Etiquetas humanas de los dominios auditados por los crons de deltas
# (orden = orden de las tarjetas; el maestro corre primero: 06:15 UTC)
_DOMINIOS_DELTAS = {
    "core-etl-v2": "Maestro (ETL)",
    "categorias-etl": "Categorías (ETL)",
    "costing-deltas": "Costos",
    "channel-deltas": "Channel",
    "orders-deltas": "Pedidos",
}
OBJETIVO_RACHA = 14  # regla de corte de la migración: 14 actas seguidas en cero

# Dominios que YA cumplieron su racha y se retiraron a propósito: su cron dejó
# de escribir acta, así que "no hay acta hoy" es lo esperado, no una falla.
#
# Siguen en `_DOMINIOS_DELTAS` para que /migracion conserve el expediente (la
# racha se calcula desde el último día CON acta, así que channel sigue
# mostrando su 22/14 cumplido), pero el vigilante de ausencias los salta.
#
# channel-deltas: retirado el 11-ago-2026 en la v0.95.1 (CHANNEL_ESPEJO_INVERSO
# =false y el cron convertido en aviso de retiro). El 12-ago a las 02:06 CDMX el
# bot avisó "Acta de Channel NO generada hoy — revisar el cron deltas en
# Railway" y mandó a revisar un cron apagado a propósito.
#
# costing-deltas y orders-deltas: retirados el 12-ago-2026 en la v0.107.0, con
# sus rachas cumplidas (27/14 y 21/14). Se apuntan AQUÍ EN EL MISMO COMMIT que
# los apaga: el vigilante corre a las 08:00 UTC del día siguiente, así que
# olvidarlo cuesta dos falsas alarmas a las 2 de la mañana — una por dominio.
_DOMINIOS_RETIRADOS = {"channel-deltas", "costing-deltas", "orders-deltas"}

# Dominios que SIGUEN AUDITANDO pero ya no tocan Slack (decisión de Eduardo,
# 14-ago-2026). Distinto de `_DOMINIOS_RETIRADOS`: aquellos no generan acta;
# estos SÍ la generan, se siguen viendo en /migracion con su racha, y solo se
# les quita el timbre.
#
# Los dos ETLs de las 06:15 son el único auditor permanente del seam y llevan
# tres días saliendo `con_deltas` por causas REALES y distintas entre sí:
# 13-ago fueron 3 altas `odoo_only` (SKUs que Odoo dio de alta y nadie cruzó a
# Woo — el alta manual que la v0.137.0 automatiza), y el 14-ago 4 altas de
# productos de Woo, que sí es fuga del seam. O sea que no se calla un falso
# positivo: se calla una señal cierta que hoy nadie está atendiendo.
#
# Queda escrito para que quien lo reactive sepa qué recupera: `seam_gap` mide lo
# que el dual-write en vivo NO cubrió, y es lo que delató los 964 pedidos
# fantasma. Revisar /migracion a mano mientras esto esté aquí.
#
# Reactivar = quitar el dominio de este conjunto.
#
# VENTANA DEL 14 AL 19-AGO-2026 — CERRADA, con su respuesta.
# Se sacó `core-etl-v2` del conjunto para comprobar si la v0.164.0 (el candado
# que sellaba antes de escribir) cerró el hueco de los padres. Sirvió:
#
#   14-ago  con_deltas  seam_gap=4   insertar=4   <- los padres que faltaban
#   15-ago  ok          seam_gap=0
#   16-ago  ok          seam_gap=0                <- tres días limpios
#   17-ago  ok          seam_gap=0
#   18-ago  con_deltas  seam_gap=31  insertar=0
#   19-ago  con_deltas  seam_gap=14  insertar=0
#
# Desde el arreglo NO ha faltado un solo producto: `insertar=0` los dos días
# rojos. Esos dos son OTRA cosa, y benigna: alguien renombró cinco familias en
# Woo (18-ago, 18:13→21:24) y al renombrar un padre WooCommerce NO manda evento
# por cada variación — ni siquiera les mueve la fecha, siguen marcadas en julio.
# El nombre de las variantes queda viejo unas horas y el ETL de medianoche lo
# cura. El acta avisa de algo que ella misma acaba de arreglar.
#
# Por eso vuelve a callarse: mientras siga así, cada renombrado con variantes
# manda una alerta roja sin nada que atender, y eso es lo que enseña a ignorar
# las alertas de verdad.
#
# Si algún día se quiere cerrar TAMBIÉN ese hueco: avisar de las variantes
# cuando se renombra el padre, que es el mismo arreglo de la v0.103.0 al revés.
# Impacto bajo — solo nombres, y se corrigen esa misma noche.
_DOMINIOS_SIN_ALERTA = {"core-etl-v2", "categorias-etl"}


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


@router.post("/backfill/product-media", dependencies=[Depends(requiere_api_key)])
def backfill_product_media(max_items: int = 1000):
    """Copia el caché histórico amazon_imagenes → enrich.product_media
    (one-shot, idempotente vía el índice único). Se corre ANTES de encender
    amazon_imagenes en KUBERA_MIRROR_TABLAS para arrancar con historial."""
    return kubera_mirror.backfill_product_media(max_items)


@router.post("/backfill/channel-submissions", dependencies=[Depends(requiere_api_key)])
def backfill_channel_submissions(tabla: str = "ml_backlog", max_items: int = 1000,
                                 offset: int = 0):
    """Reconstruye ops.channel_submissions desde las bitácoras MySQL
    (ml_backlog / amazon_backlog / ml_image_edit_backlog). Idempotente por
    detail_ref. Correr en tandas (max_items+offset) por el timeout del proxy."""
    return kubera_mirror.backfill_channel_submissions(tabla, max_items, offset)


@router.post("/backfill/channel-situacion", dependencies=[Depends(requiere_api_key)])
def backfill_channel_situacion(situacion: str = "closed", canal: str | None = None,
                               max_items: int = 5000):
    """Re-espeja a channel.listings la `situacion` que hoy tiene canal_inventario.

    Para las filas cuya situación se cambió FUERA del barrido (p. ej.
    `scripts/marcar_amazon_muertas.py`, que marca `closed` con un UPDATE
    directo): el espejo solo se dispara desde el sync, así que ese cambio nunca
    viajaba y el acta de Channel salía con_deltas sin curarse sola. Idempotente."""
    from services import channel_mirror
    return channel_mirror.backfill_situacion(situacion, canal, max_items)


@router.post("/backfill/channel-orders", dependencies=[Depends(requiere_api_key)])
def backfill_channel_orders(max_items: int = 5000, offset: int = 0):
    """Copia el histórico pedidos_ml → channel.orders (one-shot, idempotente:
    los pedidos ya espejados en vivo no se alteran — el conflicto congela
    total/comisión/skus/creado_at). Reporta cada pedido fallido. Usar
    max_items+offset en tandas (p. ej. 500) para no chocar con el timeout
    del proxy en corridas grandes."""
    return kubera_mirror.backfill_channel_orders(max_items, offset)
