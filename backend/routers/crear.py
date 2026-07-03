"""
crear.py — Alta de productos (canal "Crear Productos").

  GET  /api/crear/drafts/plan
       → Diff Odoo↔WooCommerce en modo SIMULACIÓN: qué SKUs están en Odoo y
         faltan en Woo. No escribe nada.

  POST /api/crear/drafts/sincronizar?limite=
       → Crea en WooCommerce (status=draft) los SKUs faltantes, hasta `limite`
         por corrida (progresivo: se ejecuta varias veces hasta llegar a cero).

  GET  /api/crear/candidatos?page=&per_page=&search=
       → Productos que están en Odoo pero AÚN NO listos/publicados en WooCommerce
         (status_wc != ready/publish). Son los candidatos a crear.

  POST /api/crear/productos
       → Recibe la selección (sku + odoo_id + URL de Alibaba) para dar de alta.
         La URL de Alibaba es OBLIGATORIA por producto.
         NOTA: la lógica real de creación se implementa en la siguiente fase;
         por ahora este endpoint valida la carga y la acepta.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from models.schemas import Paginacion, Producto, RespuestaProductos
from services import creacion, crear_producto, woocommerce

log = logging.getLogger("omnicanal.routers.crear")
router = APIRouter(prefix="/api/crear", tags=["crear"])

PER_PAGE_DEFAULT = 40
PER_PAGE_MAX = 100

# Canal virtual (no es un marketplace real): se muestra en su propia vista.
_CANAL_CREAR = "crear"


# ── Sincronización Odoo → WooCommerce (drafts) ─────────────────────────────────

@router.get("/drafts/plan")
async def drafts_plan(
    muestra: int = Query(50, ge=1, le=500, description="Cuántos faltantes devolver en el detalle"),
):
    """Diff Odoo↔Woo en modo simulación: no escribe nada."""
    plan = await creacion.plan_drafts()
    if not plan.get("ok"):
        raise HTTPException(502, plan.get("motivo", "No se pudo calcular el plan."))
    return {
        "ok": True,
        "odoo_total": plan["odoo_total"],
        "woo_total": plan["woo_total"],
        "faltantes_total": plan["faltantes_total"],
        "muestra": plan["faltantes"][:muestra],
    }


@router.post("/drafts/sincronizar")
async def drafts_sincronizar(
    limite: int = Query(100, ge=1, le=500, description="Máximo de drafts a crear en esta corrida"),
):
    """Crea como draft en WooCommerce los SKUs de Odoo que faltan (hasta `limite`)."""
    resultado = await creacion.sincronizar_drafts(limite)
    if not resultado.get("ok"):
        raise HTTPException(502, resultado.get("motivo", "La sincronización falló."))
    return resultado


@router.get("/candidatos", response_model=RespuestaProductos)
async def candidatos(
    page: int = Query(1, ge=1),
    per_page: int = Query(PER_PAGE_DEFAULT, ge=1, le=PER_PAGE_MAX),
    search: str | None = Query(None, description="Búsqueda por SKU o nombre"),
    skus: str | None = Query(None, description="Lista de SKUs separados por coma: solo esos se muestran"),
    orden: str = Query("valor_desc", description="campo_dirección: valor|costo|stock|tipo + _asc|_desc"),
    categoria: str | None = Query(None, description="Filtro por nombre de categoría (parcial)"),
):
    skus_filtro = [s for s in (skus or "").split(",") if s.strip()] or None
    # Índice agrupado por convención de SKU (CAT-####[-DETALLE]): una fila por
    # padre conceptual o producto único; ordenable por valor/costo/stock/tipo.
    grupos, total = await creacion.listar_candidatos_agrupados(
        page, per_page, search, skus_filtro, orden, categoria
    )

    # Datos mostrados: TODO en vivo desde WooCommerce (nombre, precio, stock,
    # estado, imagen, categoría). Tolerante: si WooCommerce falla, lista vacía.
    try:
        items_raw = await creacion.items_candidatos(grupos) if grupos else []
    except Exception as exc:  # noqa: BLE001
        log.warning("WooCommerce no disponible: %s", exc)
        items_raw = []

    items = [Producto(**i) for i in items_raw]
    total_pages = max(1, (total + per_page - 1) // per_page)
    paginacion = Paginacion(
        page=page,
        per_page=per_page,
        total=total,
        total_pages=total_pages,
        tiene_anterior=page > 1,
        tiene_siguiente=page < total_pages,
    )
    return RespuestaProductos(
        canal=_CANAL_CREAR, items=items, paginacion=paginacion,
        completo=woocommerce.drafts_completo(),
    )


# ── Alta de productos ──────────────────────────────────────────────────────────

class CrearItem(BaseModel):
    sku: str
    wc_id: int | None = None
    alibaba_url: str = Field(..., description="URL del producto en Alibaba (obligatoria)")


class CrearRequest(BaseModel):
    items: list[CrearItem]


@router.post("/productos")
async def crear_productos(req: CrearRequest):
    if not req.items:
        raise HTTPException(422, "No se recibió ningún producto para crear.")

    # La URL de Alibaba es obligatoria por producto.
    sin_url = [i.sku for i in req.items if not (i.alibaba_url or "").strip()]
    if sin_url:
        raise HTTPException(
            422,
            f"Falta la URL de Alibaba para: {', '.join(sin_url)}",
        )

    encolados = crear_producto.encolar([i.model_dump() for i in req.items])
    log.info("Creación encolada: %d de %d producto(s)", encolados, len(req.items))
    return {
        "ok": True,
        "recibidos": len(req.items),
        "encolados": encolados,
        "mensaje": (
            f"{encolados} producto(s) en proceso: Alibaba → IA → imágenes → "
            "categoría ML → WooCommerce (inprogress). Sigue el avance en esta vista."
        ),
    }


@router.get("/progreso")
async def progreso():
    """Avance de la cola de creación (en memoria)."""
    return {"items": crear_producto.progreso()}


@router.get("/categorias")
async def categorias_disponibles():
    """Nombres de TODAS las categorías de Woo (para el autocompletado)."""
    arbol = await woocommerce._cargar_categorias()
    nombres = sorted({d["name"] for d in arbol.values()}, key=str.casefold)
    return {"categorias": nombres}
