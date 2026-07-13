// api.ts — Cliente del backend FastAPI.

import type {
  CanalInfo,
  CompetenciaResp,
  CategoriaMLResult,
  ContenedorInfo,
  CostoBulkItem,
  CostoBulkResp,
  CostoDetalle,
  CostoGuardarResp,
  CostoOverrides,
  CostoPreviewResp,
  CostosListResp,
  DetalleProducto,
  GaleriaResp,
  GeneradorDef,
  GenerarIAResp,
  ProgresoImagenes,
  MejorarResp,
  PublicarPreview,
  PublicarReq,
  PublicarResultado,
  RespuestaProductos,
  StudioMetadata,
  WebhookEvento,
} from "./types";

export interface ProductoIA {
  nombre: string;
  marca?: string | null;
  modelo?: string | null;
  categoria?: string | null;
  descripcion?: string | null;
  precio?: number | null;
  costo?: number | null;
  publico?: string | null;
  atributos?: { nombre: string; valor: string }[];
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`);
  return res.json() as Promise<T>;
}

const BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8000";

async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    signal,
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${path}`);
  }
  return res.json() as Promise<T>;
}

export interface ListarParams {
  canal: string;
  page?: number;
  perPage?: number;
  search?: string;
  soloPublicados?: boolean;
  cuenta?: string | null;
  orden?: string;
  estados?: string[];
  categoria?: number | null;
  skus?: string; // "Filtrar SKUs": lista separada por comas, filtra y busca a la vez
}

export function listarProductos(
  p: ListarParams,
  signal?: AbortSignal,
): Promise<RespuestaProductos> {
  const q = new URLSearchParams();
  q.set("canal", p.canal);
  q.set("page", String(p.page ?? 1));
  q.set("per_page", String(p.perPage ?? 40));
  if (p.search) q.set("search", p.search);
  if (p.soloPublicados) q.set("solo_publicados", "true");
  if (p.cuenta) q.set("cuenta", p.cuenta);
  if (p.orden && p.orden !== "reciente") q.set("orden", p.orden);
  if (p.estados && p.estados.length) q.set("estados", p.estados.join(","));
  if (p.categoria) q.set("categoria", String(p.categoria));
  if (p.skus) q.set("skus", p.skus);
  return getJSON<RespuestaProductos>(`/api/productos?${q.toString()}`, signal);
}

export interface CategoriaWC {
  id: number;
  nombre: string;
  parent: number;
  count: number;
}

export function listarCategorias(signal?: AbortSignal): Promise<CategoriaWC[]> {
  return getJSON<CategoriaWC[]>(`/api/productos/_categorias/lista`, signal);
}

export function listarCanales(signal?: AbortSignal): Promise<CanalInfo[]> {
  return getJSON<CanalInfo[]>(`/api/canales`, signal);
}

export function studioMetadata(
  sku: string,
  wcId?: number | null,
  signal?: AbortSignal,
): Promise<StudioMetadata> {
  const q = wcId ? `?wc_id=${wcId}` : "";
  return getJSON<StudioMetadata>(
    `/api/productos/${encodeURIComponent(sku)}/studio${q}`,
    signal,
  );
}

// ── Costos: desglose + recálculo (tab COSTOS del Estudio) ────────────────────

export function costoDetalle(sku: string, signal?: AbortSignal): Promise<CostoDetalle> {
  return getJSON<CostoDetalle>(
    `/api/crear/costos/${encodeURIComponent(sku)}`,
    signal,
  );
}

// Regenerar (vista previa): calcula costo+precio SIN escribir.
export function costoPreview(sku: string, ov: CostoOverrides): Promise<CostoPreviewResp> {
  return postJSON<CostoPreviewResp>(
    `/api/crear/costos/${encodeURIComponent(sku)}/preview`, ov,
  );
}

// Guardar: persiste en costos_validados + costos_finales y sincroniza WooCommerce.
export function costoGuardar(sku: string, ov: CostoOverrides): Promise<CostoGuardarResp> {
  return postJSON<CostoGuardarResp>(
    `/api/crear/costos/${encodeURIComponent(sku)}/recalcular`, ov,
  );
}

// Tabla del menú Costos: todos los SKUs con costo + contenedor.
export interface ListarCostosParams {
  page?: number;
  perPage?: number;
  search?: string;
  contenedor?: string;
  orden?: string;
  skus?: string; // "Filtrar SKUs": lista separada por comas, filtra y busca a la vez
}

export function listarCostos(p: ListarCostosParams, signal?: AbortSignal): Promise<CostosListResp> {
  const q = new URLSearchParams();
  q.set("page", String(p.page ?? 1));
  q.set("per_page", String(p.perPage ?? 50));
  if (p.search) q.set("search", p.search);
  if (p.contenedor) q.set("contenedor", p.contenedor);
  if (p.orden) q.set("orden", p.orden);
  if (p.skus) q.set("skus", p.skus);
  return getJSON<CostosListResp>(`/api/crear/costos?${q.toString()}`, signal);
}

export function contenedoresCosto(signal?: AbortSignal): Promise<{ contenedores: ContenedorInfo[] }> {
  return getJSON<{ contenedores: ContenedorInfo[] }>("/api/crear/costos/_contenedores", signal);
}

// Fuerza el refresco del índice de catálogo + drafts de Woo (al abrir la app).
export function refrescarCatalogo(): Promise<{ ok: boolean; mensaje: string }> {
  return postJSON<{ ok: boolean; mensaje: string }>("/api/sync/catalogo", {});
}

// Busca categorías de Mercado Libre por nombre (para el picker del Estudio).
export function buscarCategoriasML(q: string, signal?: AbortSignal): Promise<{ resultados: CategoriaMLResult[] }> {
  return getJSON<{ resultados: CategoriaMLResult[] }>(
    `/api/crear/categorias-ml?q=${encodeURIComponent(q)}`, signal);
}

// Detalle de UNA categoría ML por ID (nombre + path completo) — para mostrar el
// breadcrumb cuando solo hay un ml_cat_id guardado sin niveles.
export function obtenerCategoriaML(catId: string, signal?: AbortSignal): Promise<CategoriaMLResult> {
  return getJSON<CategoriaMLResult>(`/api/crear/categorias-ml/${encodeURIComponent(catId)}`, signal);
}

export function costoBulk(
  items: CostoBulkItem[],
  opts: { margen?: number; pct_comision?: number | null; incluir_envio?: boolean; auto_cbm?: boolean; sincronizar_woo?: boolean } = {},
): Promise<CostoBulkResp> {
  return postJSON<CostoBulkResp>("/api/crear/costos/bulk", { items, ...opts });
}

// ── Crear Productos ──────────────────────────────────────────────────────────
// Candidatos: productos que están en Odoo pero aún NO listos/publicados en Woo.

export interface CandidatosParams {
  page?: number;
  perPage?: number;
  search?: string;
  skus?: string; // lista separada por comas: solo esos SKUs
  orden?: string; // valor|costo|stock|tipo + _asc|_desc
  categoria?: string; // filtro por nombre de categoría (parcial)
}

export function listarCandidatos(
  p: CandidatosParams,
  signal?: AbortSignal,
): Promise<RespuestaProductos> {
  const q = new URLSearchParams();
  q.set("page", String(p.page ?? 1));
  q.set("per_page", String(p.perPage ?? 40));
  if (p.search) q.set("search", p.search);
  if (p.skus) q.set("skus", p.skus);
  if (p.orden) q.set("orden", p.orden);
  if (p.categoria) q.set("categoria", p.categoria);
  return getJSON<RespuestaProductos>(`/api/crear/candidatos?${q.toString()}`, signal);
}

// Sincronización Odoo → WooCommerce: SKUs de Odoo que faltan en Woo → drafts.

export interface DraftFaltante {
  sku: string;
  nombre: string;
  precio: number | null;
  stock: number | null;
}

export interface DraftsPlanResp {
  ok: boolean;
  odoo_total: number;
  woo_total: number;
  faltantes_total: number;
  muestra: DraftFaltante[];
}

export function planDrafts(signal?: AbortSignal): Promise<DraftsPlanResp> {
  return getJSON<DraftsPlanResp>(`/api/crear/drafts/plan`, signal);
}

export interface SincronizarDraftsResp {
  ok: boolean;
  creados: { sku: string; wc_id: number }[];
  errores: { sku: string; error: string }[];
  faltantes_restantes: number;
  mensaje?: string;
}

export async function sincronizarDrafts(
  limite = 100,
): Promise<SincronizarDraftsResp> {
  const res = await fetch(`${BASE}/api/crear/drafts/sincronizar?limite=${limite}`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    let detalle = `API ${res.status}`;
    try {
      const j = await res.json();
      if (j?.detail) detalle = j.detail;
    } catch {
      /* sin cuerpo JSON */
    }
    throw new Error(detalle);
  }
  return res.json() as Promise<SincronizarDraftsResp>;
}

export interface CrearProductoItem {
  sku: string;
  wc_id: number | null;
  alibaba_url: string;
}

export interface CrearProductosResp {
  ok: boolean;
  recibidos: number;
  encolados?: number;
  mensaje?: string;
  pendiente?: string;
}

// Avance de la cola de creación (Alibaba → IA → imágenes → categoría → Woo)
export interface ProgresoCreacionItem {
  sku: string;
  estado: "en_cola" | "procesando" | "completado" | "error";
  paso: string;
  wc_id?: number | null;
  titulo?: string;
}

export function categoriasDisponibles(
  signal?: AbortSignal,
): Promise<{ categorias: string[] }> {
  return getJSON<{ categorias: string[] }>(`/api/crear/categorias`, signal);
}

export function progresoCreacion(
  signal?: AbortSignal,
): Promise<{ items: ProgresoCreacionItem[] }> {
  return getJSON<{ items: ProgresoCreacionItem[] }>(`/api/crear/progreso`, signal);
}

export async function crearProductos(
  items: CrearProductoItem[],
): Promise<CrearProductosResp> {
  const res = await fetch(`${BASE}/api/crear/productos`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!res.ok) {
    let detalle = `API ${res.status}`;
    try {
      const j = await res.json();
      if (j?.detail) detalle = j.detail;
    } catch {
      /* sin cuerpo JSON */
    }
    throw new Error(detalle);
  }
  return res.json() as Promise<CrearProductosResp>;
}

export function detalleProducto(
  sku: string,
  signal?: AbortSignal,
): Promise<DetalleProducto> {
  return getJSON<DetalleProducto>(
    `/api/productos/${encodeURIComponent(sku)}`,
    signal,
  );
}

export async function refrescarCanal(
  canal: string,
  sku: string,
  cuenta?: string | null,
): Promise<Record<string, unknown>> {
  const q = cuenta ? `?cuenta=${encodeURIComponent(cuenta)}` : "";
  const res = await fetch(
    `${BASE}/api/canales/${canal}/refrescar/${encodeURIComponent(sku)}${q}`,
    { method: "POST" },
  );
  if (!res.ok) throw new Error(`Refresco falló: ${res.status}`);
  return res.json();
}

// ── IA: generadores de contenido por canal ──────────────────────────
export function generadoresIA(
  canal: string,
  signal?: AbortSignal,
): Promise<{ canal: string; generadores: GeneradorDef[] }> {
  return getJSON(`/api/ia/generadores?canal=${encodeURIComponent(canal)}`, signal);
}

export interface AtributoCtx {
  nombre: string;
  valor: string;
}

export interface GenerarIAParams {
  canal: string;
  generador: string;
  producto: {
    nombre: string;
    marca?: string | null;
    categoria?: string | null;
    descripcion?: string | null;
    precio?: number | null;
    publico?: string | null;
    atributos?: AtributoCtx[];
  };
}

export async function generarIA(p: GenerarIAParams): Promise<GenerarIAResp> {
  const res = await fetch(`${BASE}/api/ia/generar`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(p),
  });
  if (!res.ok) throw new Error(`Generación IA falló: ${res.status}`);
  return res.json() as Promise<GenerarIAResp>;
}

export function mejorarIA(p: { canal: string; producto: ProductoIA }): Promise<MejorarResp> {
  return postJSON<MejorarResp>(`/api/ia/mejorar`, p);
}

export function precioCompetencia(
  p: { producto: ProductoIA; con_lista?: boolean },
): Promise<CompetenciaResp> {
  return postJSON<CompetenciaResp>(`/api/ia/precio-competencia`, {
    producto: p.producto,
    con_lista: p.con_lista ?? true,
  });
}

export function publicarPreview(req: PublicarReq): Promise<PublicarPreview> {
  return postJSON<PublicarPreview>(`/api/publicar/preview`, req);
}

export function publicarConfirmar(req: PublicarReq): Promise<PublicarResultado> {
  return postJSON<PublicarResultado>(`/api/publicar/confirmar`, req);
}

export interface NotificacionesResp {
  eventos: WebhookEvento[];
  total_hoy: number;
}

export function notificacionesWebhook(
  signal?: AbortSignal,
): Promise<NotificacionesResp> {
  return getJSON<NotificacionesResp>(`/api/webhooks/notificaciones`, signal);
}

// ── Editor de imágenes (galería WooCommerce + IA por flags) ──────────
export function galeriaProducto(
  sku: string,
  wcId?: number | null,
  signal?: AbortSignal,
): Promise<GaleriaResp> {
  const q = wcId ? `?wc_id=${wcId}` : "";
  return getJSON<GaleriaResp>(`/api/imagenes/${encodeURIComponent(sku)}${q}`, signal);
}

export interface ProcesarImagenItem {
  wc_image_id: number | null;
  src: string;
  quitar_fondo: boolean;
  traducir_texto: boolean;
  cambiar_modelo: boolean;
}

export function procesarImagenesIA(
  sku: string,
  body: { wc_id: number | null; imagenes: ProcesarImagenItem[] },
): Promise<{ ok: boolean; total: number; parent_id: number | null }> {
  return postJSON(`/api/imagenes/${encodeURIComponent(sku)}/procesar`, body);
}

export function progresoImagenes(
  sku: string,
  signal?: AbortSignal,
): Promise<ProgresoImagenes> {
  return getJSON<ProgresoImagenes>(
    `/api/imagenes/${encodeURIComponent(sku)}/progreso`,
    signal,
  );
}

export function eliminarImagenGaleria(
  sku: string,
  body: { wc_id: number | null; image_id: number },
): Promise<{ ok: boolean; image_id: number }> {
  return postJSON(`/api/imagenes/${encodeURIComponent(sku)}/eliminar`, body);
}

export interface ImagenNueva {
  filename: string;
  mime: string;
  data_b64: string;
}

export function agregarImagenes(
  sku: string,
  body: { wc_id: number | null; imagenes: ImagenNueva[] },
): Promise<{ ok: boolean; agregadas: number; imagenes: GaleriaResp["imagenes"] }> {
  return postJSON(`/api/imagenes/${encodeURIComponent(sku)}/agregar`, body);
}

// ── Guardar contenido (título/descripción/atributos) a WooCommerce (General) ──
export function guardarContenido(
  sku: string,
  body: {
    wc_id: number | null;
    titulo?: string;
    descripcion?: string;
    atributos?: { nombre: string; valor: string }[];
  },
): Promise<{ ok: boolean; sku: string; wc_id: number }> {
  return postJSON(`/api/productos/${encodeURIComponent(sku)}/contenido`, body);
}

export const API_BASE = BASE;
