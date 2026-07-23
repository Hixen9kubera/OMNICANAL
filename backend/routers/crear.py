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

  GET  /api/crear/historial            (+ /historial/{sku})
       → Bitácora PERSISTENTE de creaciones (tabla crear_logs). A diferencia de
         /progreso (memoria) y de los logs de Railway (se purgan con cada
         deploy), esto conserva el rastro completo de cada creación.

  GET  /api/crear/auditoria
       → Cruza los SKUs completados en crear_logs contra WooCommerce y reporta
         los productos creados que ya NO existen (eliminados o en papelera).
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


# ── Historial persistente de creaciones (tabla crear_logs) ─────────────────────

def _parse_detalle(v):
    import json
    try:
        return json.loads(v) if v else None
    except (TypeError, ValueError):
        return v


@router.get("/historial")
def historial(
    page: int = Query(1, ge=1),
    per_page: int = Query(PER_PAGE_DEFAULT, ge=1, le=PER_PAGE_MAX),
    sku: str | None = Query(None, description="Filtro por SKU (parcial)"),
    estado: str | None = Query(None, description="en_cola|procesando|completado|error"),
    dias: int = Query(30, ge=1, le=365),
):
    """
    Historial de creaciones: una fila por SKU con su ÚLTIMO evento registrado
    en crear_logs. Sobrevive a los deploys (los logs de Railway se purgan).
    """
    crear_producto.asegurar_schema_logs()  # la tabla puede no existir aún (deploy nuevo)
    where, params = ["creado >= UTC_TIMESTAMP() - INTERVAL %s DAY"], [dias]
    if sku:
        where.append("sku LIKE %s")
        params.append(f"%{sku}%")
    sub = (f"SELECT sku, MAX(id) AS max_id FROM crear_logs "
           f"WHERE {' AND '.join(where)} GROUP BY sku")
    est_sql, est_params = "", []
    if estado:
        est_sql = "WHERE l.estado = %s"
        est_params = [estado]

    total = db.fetch_scalar(
        f"SELECT COUNT(*) FROM crear_logs l JOIN ({sub}) u ON u.max_id = l.id {est_sql}",
        tuple(params + est_params)) or 0
    offset = (page - 1) * per_page
    rows = db.fetch_all(
        f"""SELECT l.sku, l.wc_id, l.estado, l.paso, l.detalle, l.creado
            FROM crear_logs l JOIN ({sub}) u ON u.max_id = l.id {est_sql}
            ORDER BY l.id DESC LIMIT %s OFFSET %s""",
        tuple(params + est_params + [per_page, offset]))

    items = [{**r, "detalle": _parse_detalle(r.get("detalle"))} for r in rows]
    total_pages = max(1, (total + per_page - 1) // per_page)
    return {
        "items": items,
        "paginacion": {
            "page": page, "per_page": per_page, "total": total, "total_pages": total_pages,
            "tiene_anterior": page > 1, "tiene_siguiente": page < total_pages,
        },
    }


@router.get("/historial/{sku}")
def historial_sku(
    sku: str,
    limite: int = Query(100, ge=1, le=500),
):
    """Todos los eventos de creación de UN SKU (más recientes primero)."""
    crear_producto.asegurar_schema_logs()
    rows = db.fetch_all(
        "SELECT id, wc_id, estado, paso, detalle, creado FROM crear_logs "
        "WHERE sku=%s ORDER BY id DESC LIMIT %s", (sku, limite))
    if not rows:
        raise HTTPException(404, f"No hay historial de creación para {sku}")
    return {"sku": sku,
            "eventos": [{**r, "detalle": _parse_detalle(r.get("detalle"))} for r in rows]}


@router.get("/auditoria")
async def auditoria_creaciones(dias: int = Query(30, ge=1, le=365)):
    """
    Auditoría de desapariciones: toma los SKUs COMPLETADOS en crear_logs en los
    últimos `dias` y verifica contra WooCommerce (por wc_id) que sigan
    existiendo. Distingue 'papelera' (recuperable) de 'eliminado' (borrado
    definitivo o wc_id inexistente).
    """
    crear_producto.asegurar_schema_logs()
    rows = db.fetch_all(
        """SELECT l.sku, l.wc_id, l.paso, l.creado
           FROM crear_logs l
           JOIN (SELECT sku, MAX(id) AS max_id FROM crear_logs
                 WHERE estado='completado'
                   AND creado >= UTC_TIMESTAMP() - INTERVAL %s DAY
                 GROUP BY sku) u ON u.max_id = l.id""", (dias,))
    creados = [r for r in rows if r.get("wc_id")]
    sin_wc_id = [r["sku"] for r in rows if not r.get("wc_id")]

    estados: dict[int, str] = {}
    async with woocommerce._client() as cli:
        async def _estados_lote(lote: list[dict], status: str) -> None:
            r = await cli.get("/products", params={
                "include": ",".join(str(c["wc_id"]) for c in lote),
                "per_page": 100, "status": status, "_fields": "id,status",
            })
            if r.status_code == 200:
                for p in r.json():
                    estados[int(p["id"])] = p.get("status") or status

        for i in range(0, len(creados), 100):
            await _estados_lote(creados[i:i + 100], "any")
        # status=any NO incluye la papelera: segunda pasada solo por los faltantes
        faltan = [c for c in creados if int(c["wc_id"]) not in estados]
        for i in range(0, len(faltan), 100):
            await _estados_lote(faltan[i:i + 100], "trash")

    desaparecidos, existentes = [], 0
    for c in creados:
        st = estados.get(int(c["wc_id"]))
        if st is None:
            desaparecidos.append({**c, "situacion": "eliminado"})
        elif st == "trash":
            desaparecidos.append({**c, "situacion": "papelera"})
        else:
            existentes += 1
    return {
        "ok": True, "dias": dias, "creados": len(rows), "existentes": existentes,
        "desaparecidos": desaparecidos, "sin_wc_id": sin_wc_id,
    }


@router.get("/categorias")
async def categorias_disponibles():
    """Solo categorías PADRE (nivel raíz) de Woo, para el autocompletado."""
    arbol = await woocommerce._cargar_categorias()
    nombres = sorted(
        {d["name"] for d in arbol.values() if not d.get("parent")},
        key=str.casefold,
    )
    return {"categorias": nombres}


class GuardarCategoriaML(BaseModel):
    wc_id: int
    category_id: str = Field(..., description="ID de categoría ML, ej. MLM447349")


@router.post("/categoria-ml")
async def guardar_categoria_ml(req: GuardarCategoriaML):
    """
    Persiste en WooCommerce la categoría ML ELEGIDA en el panel (Estudio). Escribe
    las MISMAS metas que lee el publicador (`publicar_ready.construir_prod`):
    `ml_categoria_id` es la elección HUMANA y MANDA sobre el predictor de Crear
    (`ml_category_id`). Sin este guardado, el picker solo cambiaba estado local y
    la categoría no persistía (bug 2026-07-23).
    """
    import httpx
    import json as _json
    cat_id = (req.category_id or "").strip()
    if not cat_id:
        raise HTTPException(422, "category_id vacío.")
    async with httpx.AsyncClient(base_url="https://api.mercadolibre.com", timeout=15.0) as cli:
        r = await cli.get(f"/categories/{cat_id}")
    if r.status_code != 200:
        raise HTTPException(404, f"Categoría {cat_id} no encontrada en Mercado Libre.")
    d = r.json()
    niveles = [{"id": p.get("id", ""), "name": p.get("name", "")}
               for p in (d.get("path_from_root") or [])]
    ruta = " > ".join(n["name"] for n in niveles)
    nombre_hoja = d.get("name") or (niveles[-1]["name"] if niveles else cat_id)
    dominio_id = (d.get("settings") or {}).get("catalog_domain") or ""
    meta = [
        # La elección del panel MANDA: se escribe tanto la llave humana
        # (ml_categoria_id) como la del predictor (ml_category_id) para que
        # queden alineadas y no se contradigan en una re-lectura.
        {"key": "ml_categoria_id", "value": cat_id},
        {"key": "ml_category_id", "value": cat_id},
        {"key": "ml_category_name", "value": nombre_hoja},
        {"key": "ml_categoria_path", "value": ruta},
        {"key": "ml_categoria_niveles", "value": _json.dumps(niveles, ensure_ascii=False)},
        {"key": "ml_dominio_id", "value": dominio_id},
    ]
    # niveles legibles (los que pinta el breadcrumb del Estudio desde postmeta)
    for i, n in enumerate(niveles[:5], start=1):
        meta.append({"key": f"ml_categoria_nivel_{i}", "value": n["name"]})
    async with woocommerce._client() as cli:
        resp = await cli.put(f"/products/{req.wc_id}", json={"meta_data": meta}, timeout=60.0)
        if resp.status_code not in (200, 201):
            raise HTTPException(502, f"WooCommerce HTTP {resp.status_code}: {resp.text[:150]}")
    log.info("Categoría ML guardada: wc_id=%s → %s (%s)", req.wc_id, cat_id, ruta)
    return {"ok": True, "wc_id": req.wc_id, "category_id": cat_id,
            "name": nombre_hoja, "path": ruta, "niveles": niveles,
            "domain": d.get("settings", {}).get("catalog_domain") or ""}


@router.get("/categorias-ml/{cat_id}")
async def obtener_categoria_ml(cat_id: str):
    """
    Detalle de UNA categoría ML por ID: nombre, path completo (Nivel1 > … > hoja)
    y dominio. Público (sin token). Lo usa el picker del Estudio para mostrar el
    breadcrumb completo cuando ya hay un ml_cat_id guardado pero sin niveles en
    el postmeta de Woo (categorías asignadas por un proceso viejo, solo con ID).
    """
    import httpx
    async with httpx.AsyncClient(base_url="https://api.mercadolibre.com", timeout=15.0) as cli:
        r = await cli.get(f"/categories/{cat_id}")
    if r.status_code != 200:
        raise HTTPException(404, f"Categoría {cat_id} no encontrada.")
    d = r.json()
    niveles = [p.get("name", "") for p in (d.get("path_from_root") or [])]
    return {
        "category_id": cat_id,
        "name": d.get("name") or "",
        "domain": "",  # /categories/{id} no trae dominio; solo lo trae domain_discovery
        "path": " > ".join(niveles),
    }


@router.get("/categorias-ml")
async def buscar_categorias_ml(
    q: str = Query(..., min_length=2, description="Nombre a buscar"),
    limite: int = Query(8, ge=1, le=15),
):
    """
    Busca categorías de Mercado Libre por NOMBRE (domain_discovery) y devuelve, por
    cada una: category_id, nombre, path (Nivel1 > … > hoja) y dominio. Para el picker
    de categoría del Estudio (define la comisión del cálculo del costo).
    """
    import asyncio
    import httpx
    from config import settings
    from services import meli

    # Token de ml_tokens (Fernet, llave en .env). domain_discovery y /categories son
    # PÚBLICOS, así que si el token está vencido/inválido reintentamos sin auth.
    token = meli._access_token(costos.DEFAULT_ACCOUNT) or meli._access_token()

    async with httpx.AsyncClient(base_url="https://api.mercadolibre.com", timeout=20.0) as cli:
        async def _get(path: str, params: dict | None = None):
            h = {"Authorization": f"Bearer {token}"} if token else {}
            r = await cli.get(path, params=params, headers=h)
            if r.status_code in (401, 403) and h:  # token inválido → público
                r = await cli.get(path, params=params)
            return r

        try:
            r = await _get(f"/sites/{settings.ml_site_id}/domain_discovery/search",
                           {"limit": limite, "q": q})
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Error consultando Mercado Libre: {exc}")
        cands = r.json() if r.status_code == 200 else []

        vistos: set[str] = set()
        base: list[dict] = []
        for d in cands:
            cid = d.get("category_id")
            if not cid or cid in vistos:
                continue
            vistos.add(cid)
            base.append({
                "category_id": cid,
                "name": d.get("category_name") or "",
                "domain": d.get("domain_name") or "",
                "path": "",
            })

        async def _path(cid: str) -> str:
            try:
                rc = await _get(f"/categories/{cid}")
                if rc.status_code == 200:
                    pr = rc.json().get("path_from_root") or []
                    return " > ".join(p.get("name", "") for p in pr)
            except Exception:  # noqa: BLE001
                pass
            return ""

        paths = await asyncio.gather(*[_path(b["category_id"]) for b in base])
        for b, p in zip(base, paths):
            b["path"] = p or b["name"]

    return {"resultados": base}


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
    pct_comision: float | None = None  # comisión ML manual (decimal, ej. 0.15)
    incluir_envio: bool = True
    margen: float = costos.MARGEN_DEFAULT
    # auto_cbm: deriva costo_cbm de las dims (× tarifa) salvo que venga explícito.
    auto_cbm: bool = True
    sincronizar_woo: bool = True

    def _overrides(self) -> dict:
        ov = self.model_dump(
            exclude={"incluir_envio", "margen", "auto_cbm", "sincronizar_woo"})
        return {k: v for k, v in ov.items() if v is not None}


@router.get("/costos/_contenedores")
def costos_contenedores():
    """Contenedores disponibles (para el filtro de la tabla de costos).
    Definido ANTES de /costos/{sku} para que no lo capture la ruta con parámetro."""
    rows = db.fetch_all(
        "SELECT contenedor, COUNT(*) AS n FROM costos_validados "
        "WHERE contenedor IS NOT NULL AND contenedor <> '' "
        "GROUP BY contenedor ORDER BY contenedor")
    return {"contenedores": [{"contenedor": r["contenedor"], "n": int(r["n"])} for r in rows]}


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
            422, "No se pudo calcular: falta el costo (costo producto/dimensiones), o no se encontró la comisión de la categoría — ingresa la Comisión ML (%).")
    return {"ok": True, "sku": sku, "calculo": calc}


async def _categoria_ml_meta(cat_id: str) -> list[dict]:
    """
    Meta de Woo para la categoría ML elegida en el costeo: id, nombre y niveles
    (ml_categoria_nivel_1..5) — lo que lee wp_db.metadata_producto() para pintar
    el breadcrumb en el Estudio. Usa el endpoint PÚBLICO de ML (sin token).
    """
    import httpx
    try:
        async with httpx.AsyncClient(base_url="https://api.mercadolibre.com", timeout=15.0) as cli:
            r = await cli.get(f"/categories/{cat_id}")
        if r.status_code != 200:
            return [{"key": "ml_category_id", "value": cat_id}]
        d = r.json()
        niveles = [p.get("name", "") for p in (d.get("path_from_root") or [])]
        meta = [
            {"key": "ml_category_id", "value": cat_id},
            {"key": "ml_category_name", "value": d.get("name") or ""},
            {"key": "ml_categoria_path", "value": " > ".join(niveles)},
        ]
        for i, nombre in enumerate(niveles[:5], start=1):
            meta.append({"key": f"ml_categoria_nivel_{i}", "value": nombre})
        return meta
    except Exception:  # noqa: BLE001
        return [{"key": "ml_category_id", "value": cat_id}]


async def _sync_woo_costo(sku: str, fila: dict) -> bool:
    """Escribe a WooCommerce precio regular/oferta + costo + peso/dimensiones + categoría ML."""
    p = await woocommerce.obtener_producto_por_sku(sku)
    wc_id = p.get("wc_id") if p else None
    if not wc_id:
        return False
    meta = [
        {"key": "wc_kam_costo_envio", "value": str(fila["costo_fee_envio"])},
        {"key": "wc_kam_costo_comision", "value": str(fila["costo_comision"])},
    ]
    if fila.get("costo_unitario") is not None:
        meta.append({"key": "costo", "value": f"{float(fila['costo_unitario']):.2f}"})
    if fila.get("ml_cat_id"):
        meta.extend(await _categoria_ml_meta(str(fila["ml_cat_id"])))
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

        # Padre variable (ej. mismo producto en varios colores): WooCommerce
        # muestra en las listas el precio de las VARIANTES, no el del padre —
        # así que sin esto, un padre recién costeado sigue mostrando el precio
        # placeholder de sus variantes. Se replica el MISMO costo/precio a todas
        # (misma pieza física, solo cambia color/talla).
        if (p or {}).get("tipo") == "variable":
            try:
                rv = await cli.get(f"/products/{wc_id}/variations",
                                    params={"per_page": 100, "_fields": "id"})
                if rv.status_code == 200:
                    ids = [v["id"] for v in rv.json()]
                    if ids:
                        await cli.post(
                            f"/products/{wc_id}/variations/batch",
                            json={"update": [
                                {"id": vid, "regular_price": payload["regular_price"],
                                 "sale_price": payload["sale_price"]}
                                for vid in ids
                            ]},
                            timeout=120.0,
                        )
            except Exception as exc:  # noqa: BLE001
                log.warning("No se pudo replicar precio a variantes de %s (wc_id=%s): %s",
                           sku, wc_id, exc)
    return True


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
            422, "No se pudo recalcular: falta el costo (costo producto/dimensiones), o no hay comisión para la categoría — ingresa la Comisión ML (%).")
    synced = await _sync_woo_costo(sku, fila) if req.sincronizar_woo else False
    return {"ok": True, "sku": sku, "finales": fila, "sincronizado_woo": synced}


# ── Costos: listado (tabla del menú Costos) + regeneración en bulk ──────────────

_ORDEN_COSTOS = {
    "reciente": "v.created_at DESC",
    "sku_asc": "v.sku ASC",
    "sku_desc": "v.sku DESC",
    "costo_desc": "v.costo_total DESC",
    "costo_asc": "v.costo_total ASC",
    "contenedor": "v.contenedor ASC, v.sku ASC",
}


@router.get("/costos")
def costos_listado(
    page: int = Query(1, ge=1),
    per_page: int = Query(PER_PAGE_DEFAULT, ge=1, le=PER_PAGE_MAX),
    search: str | None = Query(None),
    contenedor: str | None = Query(None),
    orden: str = Query("reciente"),
    skus: str | None = Query(None, description="Lista de SKUs/términos separados por coma: filtra y busca a la vez"),
):
    """Tabla de costos por SKU (costos_validados + precios + nombre + contenedor)."""
    where, params = [], []
    if search:
        where.append("(v.sku LIKE %s OR p.nombre LIKE %s)")
        params += [f"%{search}%", f"%{search}%"]
    skus_lista = [s.strip() for s in (skus or "").split(",") if s.strip()]
    if skus_lista:
        or_grupo = " OR ".join(["(v.sku LIKE %s OR p.nombre LIKE %s)"] * len(skus_lista))
        where.append(f"({or_grupo})")
        for t in skus_lista:
            like_t = f"%{t}%"
            params += [like_t, like_t]
    if contenedor:
        where.append("v.contenedor = %s")
        params.append(contenedor)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    orden_sql = _ORDEN_COSTOS.get(orden, _ORDEN_COSTOS["reciente"])

    total = db.fetch_scalar(
        f"SELECT COUNT(*) FROM costos_validados v "
        f"LEFT JOIN productos p ON p.sku = v.sku {where_sql}", tuple(params)) or 0
    offset = (page - 1) * per_page
    rows = db.fetch_all(
        f"""SELECT v.sku, p.nombre, v.contenedor,
                   v.largo, v.alto, v.ancho, v.peso,
                   v.costo_producto, v.costo_cbm, v.costo_total,
                   f.costo_unitario, f.precio_base, f.precio_sugerido,
                   f.costo_comision, f.costo_fee_envio, f.ml_cat_id
            FROM costos_validados v
            LEFT JOIN productos p ON p.sku = v.sku
            LEFT JOIN costos_finales f ON f.sku = v.sku
            {where_sql} ORDER BY {orden_sql} LIMIT %s OFFSET %s""",
        tuple(params + [per_page, offset]))

    def _f(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    items = []
    for r in rows:
        largo, ancho, alto = _f(r["largo"]), _f(r["ancho"]), _f(r["alto"])
        vol = round(largo * ancho * alto / 1_000_000, 4) if (largo and ancho and alto) else None
        items.append({
            "sku": r["sku"], "nombre": r.get("nombre"), "contenedor": r.get("contenedor"),
            "largo": largo, "ancho": ancho, "alto": alto, "peso": _f(r["peso"]),
            "volumen_m3": vol,
            "costo_producto": _f(r["costo_producto"]),
            "costo_cbm": _f(r["costo_cbm"]),
            "costo_unitario": _f(r["costo_unitario"]) or _f(r["costo_total"]),
            "precio_base": _f(r["precio_base"]),
            "precio_sugerido": _f(r["precio_sugerido"]),
            "ml_cat_id": r.get("ml_cat_id"),
        })
    total_pages = max(1, (total + per_page - 1) // per_page)
    return {
        "items": items,
        "paginacion": {
            "page": page, "per_page": per_page, "total": total, "total_pages": total_pages,
            "tiene_anterior": page > 1, "tiene_siguiente": page < total_pages,
        },
    }


class BulkItem(BaseModel):
    sku: str
    costo_producto: float | None = None
    largo: float | None = None
    alto: float | None = None
    ancho: float | None = None
    peso: float | None = None
    ml_cat_id: str | None = None
    pct_comision: float | None = None


class BulkCostos(BaseModel):
    items: list[BulkItem]
    incluir_envio: bool = True
    margen: float = costos.MARGEN_DEFAULT
    pct_comision: float | None = None  # comisión ML manual global (decimal)
    auto_cbm: bool = True
    sincronizar_woo: bool = True


@router.post("/costos/bulk")
async def costos_bulk(req: BulkCostos):
    """
    Regenera el costo/precio de VARIOS SKUs (con las medidas y costo que llegan por
    fila). Reconstruye CBM→costo→precios, persiste en DB + log, y (opcional) Woo.
    """
    resultados = []
    for it in req.items:
        overrides = {k: v for k, v in it.model_dump(exclude={"sku"}).items() if v is not None}
        # comisión global del bulk si la fila no trae una propia
        if "pct_comision" not in overrides and req.pct_comision is not None:
            overrides["pct_comision"] = req.pct_comision
        try:
            fila = await run_in_threadpool(
                costos.recalcular, it.sku, overrides,
                req.incluir_envio, req.margen, costos.DEFAULT_ACCOUNT, req.auto_cbm)
        except Exception as exc:  # noqa: BLE001
            resultados.append({"sku": it.sku, "ok": False, "error": str(exc)[:120]})
            continue
        if not fila:
            resultados.append({"sku": it.sku, "ok": False,
                               "error": "sin costo base o sin comisión de categoría (ingresar Comisión %)"})
            continue
        synced = False
        if req.sincronizar_woo:
            try:
                synced = await _sync_woo_costo(it.sku, fila)
            except Exception as exc:  # noqa: BLE001
                resultados.append({"sku": it.sku, "ok": True, "sincronizado_woo": False,
                                   "aviso": f"DB ok, Woo falló: {str(exc)[:80]}",
                                   "costo_unitario": fila.get("costo_unitario"),
                                   "precio_sugerido": fila.get("precio_sugerido")})
                continue
        resultados.append({
            "sku": it.sku, "ok": True, "sincronizado_woo": synced,
            "costo_unitario": fila.get("costo_unitario"),
            "precio_base": fila.get("precio_base"),
            "precio_sugerido": fila.get("precio_sugerido"),
            "costo_cbm": fila.get("costo_cbm"),
        })
    ok = sum(1 for r in resultados if r.get("ok"))
    return {"ok": True, "total": len(resultados), "exitosos": ok, "resultados": resultados}
