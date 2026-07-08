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
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from models.schemas import Paginacion, Producto, RespuestaProductos
from services import costos, creacion, crear_producto, db, woocommerce

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
    """Solo categorías PADRE (nivel raíz) de Woo, para el autocompletado."""
    arbol = await woocommerce._cargar_categorias()
    nombres = sorted(
        {d["name"] for d in arbol.values() if not d.get("parent")},
        key=str.casefold,
    )
    return {"categorias": nombres}


# ── Costos: consulta + recálculo manual ────────────────────────────────────────

class RecalcularCostos(BaseModel):
    """Overrides editables para el recálculo manual del costo/precio."""
    costo_producto: float | None = None
    costo_cbm: float | None = None
    largo: float | None = None
    alto: float | None = None
    ancho: float | None = None
    peso: float | None = None
    ml_cat_id: str | None = None
    incluir_envio: bool = True
    margen: float = costos.MARGEN_DEFAULT
    # auto_cbm: deriva costo_cbm de las dims (× tarifa) salvo que venga explícito.
    auto_cbm: bool = True
    sincronizar_woo: bool = True

    def _overrides(self) -> dict:
        ov = self.model_dump(
            exclude={"incluir_envio", "margen", "auto_cbm", "sincronizar_woo"})
        return {k: v for k, v in ov.items() if v is not None}


@router.get("/costos/{sku}")
async def costos_detalle(sku: str):
    """
    Desglose de costo/precio de un SKU para el container de Costos.
    Combina costos_finales (precio) + costos_validados (costo base editable).
    """
    cf = db.fetch_one("SELECT * FROM costos_finales WHERE sku=%s", (sku,))
    cv = db.fetch_one("SELECT * FROM costos_validados WHERE sku=%s", (sku,))
    logs = db.fetch_all(
        "SELECT accion, origen, created_at FROM costos_logs "
        "WHERE sku=%s ORDER BY id DESC LIMIT 10", (sku,))
    if not cf and not cv:
        raise HTTPException(404, f"No hay datos de costo para {sku}")
    return {"sku": sku, "finales": cf, "validados": cv, "logs": logs,
            "constantes": {"margen": costos.MARGEN_DEFAULT, "iva": costos.IVA_RATE,
                           "descuento": costos.DESCUENTO_BASE}}


@router.post("/costos/{sku}/preview")
async def costos_preview(sku: str, req: RecalcularCostos):
    """
    Vista previa del recálculo (dims → CBM → costo → precios) SIN escribir nada.
    Lo usa el botón "Regenerar" del tab de Costos para mostrar el antes/después.
    """
    calc = await run_in_threadpool(
        costos.computar, sku, req._overrides(),
        req.incluir_envio, req.margen, costos.DEFAULT_ACCOUNT, req.auto_cbm)
    if not calc:
        raise HTTPException(
            422, "No se pudo calcular (falta costo base o categoría ML válida).")
    return {"ok": True, "sku": sku, "calculo": calc}


@router.post("/costos/{sku}/recalcular")
async def costos_recalcular(sku: str, req: RecalcularCostos):
    """
    Recálculo MANUAL: aplica los overrides, deriva CBM de las dims, recalcula precio
    con la comisión ML, PERSISTE en costos_validados + costos_finales, deja log y
    (opcional) sincroniza a WooCommerce (precios + costo + peso/dimensiones).
    """
    fila = await run_in_threadpool(
        costos.recalcular, sku, req._overrides(),
        req.incluir_envio, req.margen, costos.DEFAULT_ACCOUNT, req.auto_cbm)
    if not fila:
        raise HTTPException(
            422, "No se pudo recalcular (falta costo base o categoría ML válida).")

    synced = False
    if req.sincronizar_woo:
        p = await woocommerce.obtener_producto_por_sku(sku)
        wc_id = p.get("wc_id") if p else None
        if wc_id:
            meta = [
                {"key": "wc_kam_costo_envio", "value": str(fila["costo_fee_envio"])},
                {"key": "wc_kam_costo_comision", "value": str(fila["costo_comision"])},
            ]
            if fila.get("costo_unitario") is not None:
                meta.append({"key": "costo", "value": f"{float(fila['costo_unitario']):.2f}"})
            payload: dict = {
                "regular_price": f"{float(fila['precio_base']):.2f}",
                "sale_price": f"{float(fila['precio_sugerido']):.2f}",
                "meta_data": meta,
            }
            if fila.get("peso") is not None:
                payload["weight"] = f"{float(fila['peso']):.3f}"
            if fila.get("largo") and fila.get("ancho") and fila.get("alto"):
                payload["dimensions"] = {
                    "length": f"{float(fila['largo']):.2f}",
                    "width": f"{float(fila['ancho']):.2f}",
                    "height": f"{float(fila['alto']):.2f}",
                }
            async with woocommerce._client() as cli:
                r = await cli.put(f"/products/{wc_id}", json=payload, timeout=120.0)
                r.raise_for_status()
            synced = True

    return {"ok": True, "sku": sku, "finales": fila, "sincronizado_woo": synced}
