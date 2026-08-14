// api.ts — Cliente del backend FastAPI.

import { haySesion, refrescar, token } from "./sesion";

import type {
  CanalInfo,
  CompetenciaCorrida,
  CompetenciaEstado,
  CompetenciaSku,
  CompetenciaTabla,
  CompetenciaVista,
  CompetenciaDetalleSku,
  CompetenciaTerminosSub,
  CompetenciaSkusSub,
  CompetenciaSugerenciaSub,
  CompetenciaSugerenciaSku,
  RankingCategoriaResp,
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
  ResolverEdicion,
  ResolverEstado,
  ResolverFila,
  ResolverGuardado,
  ResolverSkuBuscado,
  ResolverTotales,
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
  ml_cat_id?: string | null;
  sku?: string | null;
  atributos?: { nombre: string; valor: string }[];
}

/**
 * Error de la API que CONSERVA el `detail` del backend. FastAPI explica en él
 * qué falta (p. ej. "no se encontró la comisión de la categoría — ingresa la
 * Comisión ML (%)"); antes se perdía y el usuario solo veía "no se pudo".
 * `message` no cambia, para no alterar lo que ya se muestra en otras vistas.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail?: string;
  constructor(status: number, path: string, detail?: string) {
    super(`API ${status}: ${path}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function errorDeRespuesta(res: Response, path: string): Promise<ApiError> {
  let detail: string | undefined;
  try {
    const j = (await res.json()) as { detail?: unknown };
    if (typeof j.detail === "string" && j.detail.trim()) detail = j.detail.trim();
  } catch {
    // cuerpo vacío o no-JSON: nos quedamos con el mensaje genérico
  }
  return new ApiError(res.status, path, detail);
}

/** Texto para el usuario: lo que explicó el backend, o el respaldo genérico. */
export function mensajeDeError(e: unknown, respaldo: string): string {
  return e instanceof ApiError && e.detail ? e.detail : respaldo;
}

/**
 * Cabeceras de toda llamada al backend, con el token de sesión si lo hay.
 *
 * Se exporta porque en el archivo quedan `fetch()` sueltos que no pasan por
 * estos ayudantes; todos deben usar esto o se romperán el día que se encienda
 * el enforcement (AUTH_ENFORCED=true).
 */
export function cabeceras(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { Accept: "application/json", ...extra };
  const t = token();
  if (t) h.Authorization = `Bearer ${t}`;
  return h;
}

/**
 * `fetch` que sobrevive a un token vencido.
 *
 * El token de Supabase dura 1 hora y `lib/sesion.ts` lo renueva solo con un
 * temporizador. Esta es la SEGUNDA red: si aun así llega un 401 —la laptop
 * durmió, el navegador congeló la pestaña, el reloj se fue— se renueva y se
 * reintenta UNA vez. Sin esto, el usuario ve un error y pierde lo que estaba
 * haciendo por algo que se arregla solo en 300 ms.
 *
 * Una sola vez, nunca en bucle: si el segundo intento también da 401, el
 * problema no es el token y hay que dejar que el error suba.
 *
 * SE EXPORTA, Y HAY QUE USARLO SIEMPRE. Varias páginas llamaban la API con
 * `fetch()` pelón; el día que se encendió el enforcement (5-ago) todas
 * respondieron 401 a la vez: Análisis, Categorías, Estrellas, Operaciones y
 * Migración quedaron en blanco pese a haber iniciado sesión. Un `fetch` directo
 * al backend NO manda el token.
 */
export async function fetchSesion(url: string, init: RequestInit = {},
                                  extra: Record<string, string> = {}): Promise<Response> {
  const armar = (): RequestInit => ({ ...init, headers: cabeceras(extra) });
  const res = await fetch(url, armar());
  if (res.status !== 401 || !haySesion()) return res;
  if (!(await refrescar())) return res;
  return fetch(url, armar());
}

/**
 * Descarga un archivo del backend CON la sesión puesta.
 *
 * Un `<a href="…/excel">` no sirve: el navegador navega solo, sin el
 * `Authorization`, y desde el 5-ago (enforcement) eso devuelve 401 — el usuario
 * bajaba un archivo de error en vez del reporte. Se pide por fetch, se arma un
 * blob y se dispara la descarga desde el propio navegador.
 */
export async function descargar(url: string, nombreSugerido: string): Promise<void> {
  const res = await fetchSesion(url, { cache: "no-store" });
  if (!res.ok) throw await errorDeRespuesta(res, url);
  const blob = await res.blob();
  // El nombre real lo manda el backend en Content-Disposition; si no viene, se
  // usa el sugerido para no bajar un archivo llamado "descarga".
  const cd = res.headers.get("Content-Disposition") || "";
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  const nombre = m ? decodeURIComponent(m[1]) : nombreSugerido;

  const enlace = document.createElement("a");
  enlace.href = URL.createObjectURL(blob);
  enlace.download = nombre;
  document.body.appendChild(enlace);
  enlace.click();
  enlace.remove();
  // Liberar el objeto: sin esto el blob se queda en memoria toda la sesión.
  setTimeout(() => URL.revokeObjectURL(enlace.href), 30_000);
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetchSesion(
    `${BASE}${path}`,
    { method: "POST", body: JSON.stringify(body) },
    { "Content-Type": "application/json" },
  );
  if (!res.ok) throw await errorDeRespuesta(res, path);
  return res.json() as Promise<T>;
}

const BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8000";

async function getJSON<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetchSesion(`${BASE}${path}`, { signal, cache: "no-store" });
  if (!res.ok) {
    throw await errorDeRespuesta(res, path);
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
  vista?: "productos" | "crear" | "omnicanal";
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
  // Qué pestaña pide el listado: reparte el catálogo por estado de WooCommerce.
  // productos = publish/pending/ready · crear = draft/inprogress · omnicanal = todos.
  if (p.vista) q.set("vista", p.vista);
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

// Persiste la categoría ML elegida en el panel (escribe ml_categoria_id + niveles
// en WooCommerce — la elección humana que MANDA sobre el predictor al publicar).
export function guardarCategoriaML(
  wcId: number,
  categoryId: string,
): Promise<{ ok: boolean; category_id: string; name: string; path: string; niveles: { id: string; name: string }[]; domain: string }> {
  return postJSON(`/api/crear/categoria-ml`, { wc_id: wcId, category_id: categoryId });
}

// Guarda el código de barras / GTIN del producto (WooCommerce `_barcode`) — lo usa
// el publisher ML cuando la categoría exige GTIN real (ej. colchones en SANCOR).
export function guardarGtin(
  wcId: number,
  gtin: string,
): Promise<{ ok: boolean; gtin: string | null }> {
  return postJSON(`/api/crear/gtin`, { wc_id: wcId, gtin });
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
    headers: cabeceras(),
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
  permitirSinCosto = false,
): Promise<CrearProductosResp> {
  const res = await fetch(`${BASE}/api/crear/productos`, {
    method: "POST",
    headers: cabeceras({ "Content-Type": "application/json" }),
    body: JSON.stringify({ items, permitir_sin_costo: permitirSinCosto }),
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
    headers: cabeceras({ "Content-Type": "application/json" }),
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

/* ── CONTENIDO POR CANAL (enrich.channel_content) ─────────────────────
 * `guardarContenido` de arriba escribe a WooCommerce y es SOLO del canal
 * General. Estas dos son el resto de los canales: el contenido editado por
 * canal (título, descripción, bullets, highlights, atributos) sube al servidor
 * en vez de quedarse en el localStorage de este navegador.
 *
 * Las llaves del objeto son las CANÓNICAS del panel — el backend traduce a
 * `item_name` / `productName` / `goodsName` al publicar.
 */

export interface ContenidoCanal {
  existe: boolean;
  sku: string;
  canal: string;
  cuenta: string;
  categoria?: string | null;
  contenido: Record<string, unknown>;
  origen: Record<string, string>;
  updated_at?: string | null;
}

async function putJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetchSesion(
    `${BASE}${path}`,
    { method: "PUT", body: JSON.stringify(body) },
    { "Content-Type": "application/json" },
  );
  if (!res.ok) throw await errorDeRespuesta(res, path);
  return res.json() as Promise<T>;
}

export function guardarContenidoCanal(
  sku: string,
  canal: string,
  contenido: Record<string, unknown>,
  opts?: { cuenta?: string; origen?: Record<string, string>; categoria?: string },
): Promise<{ ok: boolean; sku: string; canal: string; campos: number }> {
  const q = opts?.cuenta ? `?cuenta=${encodeURIComponent(opts.cuenta)}` : "";
  return putJSON(
    `/api/productos/${encodeURIComponent(sku)}/canal/${encodeURIComponent(canal)}/contenido${q}`,
    { contenido, origen: opts?.origen, categoria: opts?.categoria },
  );
}

export function leerContenidoCanal(
  sku: string,
  canal: string,
  cuenta = "",
): Promise<ContenidoCanal> {
  const q = cuenta ? `?cuenta=${encodeURIComponent(cuenta)}` : "";
  return getJSON<ContenidoCanal>(
    `/api/productos/${encodeURIComponent(sku)}/canal/${encodeURIComponent(canal)}/contenido${q}`,
  );
}

/** El semáforo: qué le falta a un SKU para publicarse en un canal. */
export interface FaltantesCanal {
  /** `sin_requisitos` = NO sabemos (esa categoría no se ha leído del canal).
   *  NO es lo mismo que `ok`, y el panel no debe pintarlo en verde. */
  estado: "ok" | "incompleto" | "sin_requisitos";
  faltan: { campo: string; canonico: string | null; label: string }[];
  /** Los que el publicador llena solo con su respaldo: ni verde ni rojo. */
  automaticos: { campo: string; valor: unknown }[];
  categoria: string | null;
  leido_at: string | null;
}

export function faltantesCanal(
  sku: string,
  canal: string,
  cuenta = "",
): Promise<FaltantesCanal> {
  const q = cuenta ? `?cuenta=${encodeURIComponent(cuenta)}` : "";
  return getJSON<FaltantesCanal>(
    `/api/productos/${encodeURIComponent(sku)}/canal/${encodeURIComponent(canal)}/faltantes${q}`,
  );
}

export const API_BASE = BASE;

/* ── VENTAS ──────────────────────────────────────────────────────── */

import type { VentasResumen } from "./types";

export function ventasHorario(
  params: {
    canal: string;              // "general" | "mercado_libre"
    cuenta?: string | null;     // BEKURA | SANCORFASHION | null (todas)
    desde?: string;             // YYYY-MM-DD
    hasta?: string;
  },
  signal?: AbortSignal,
): Promise<VentasResumen> {
  const q = new URLSearchParams();
  q.set("canal", params.canal);
  if (params.cuenta) q.set("cuenta", params.cuenta);
  if (params.desde) q.set("desde", params.desde);
  if (params.hasta) q.set("hasta", params.hasta);
  return getJSON(`/api/ventas/horario?${q.toString()}`, signal);
}

/* ── Tipo de producto de AMAZON (picker como el de categorías ML) ── */

export interface TipoAmazon {
  name: string;
  label: string;
}

export function tipoAmazonActual(
  sku: string,
  wcId: number,
  signal?: AbortSignal,
): Promise<{ product_type: string | null; origen: "panel" | "historial" | "auto" }> {
  return getJSON(
    `/api/publicar/amazon/tipo?sku=${encodeURIComponent(sku)}&wc_id=${wcId}`,
    signal,
  );
}

export function buscarTiposAmazon(
  q: string,
  signal?: AbortSignal,
): Promise<{ tipos: TipoAmazon[] }> {
  return getJSON(`/api/publicar/amazon/tipos?q=${encodeURIComponent(q)}`, signal);
}

export function guardarTipoAmazon(
  sku: string,
  wcId: number,
  productType: string,
): Promise<{ ok: boolean; product_type: string }> {
  return postJSON(`/api/publicar/amazon/tipo`, {
    sku, wc_id: wcId, product_type: productType,
  });
}

/* ── CATEGORÍA DE TIKTOK ───────────────────────────────────────────────
 * Cada canal tiene su propio mundo de categorías y no se traducen entre sí:
 * ML usa `MLM…`, Amazon un `productType` de lista plana y TikTok un id numérico
 * de HOJA. La elección del panel manda sobre el recomendador del canal.
 */

export interface CategoriaTikTok {
  category_id: string;
  name?: string | null;
  path?: string | null;
}

export function buscarCategoriasTikTok(
  q: string,
  signal?: AbortSignal,
): Promise<{ canal: string; resultados: CategoriaTikTok[] }> {
  return getJSON(`/api/productos/categorias/tiktok?q=${encodeURIComponent(q)}`, signal);
}

/** `origen`: `panel` (alguien la eligió) · `canal` (la que tiene publicada) · null. */
export function categoriaTikTokActual(
  sku: string,
  signal?: AbortSignal,
): Promise<CategoriaTikTok & { origen: string | null }> {
  return getJSON(
    `/api/productos/${encodeURIComponent(sku)}/canal/tiktok/categoria`,
    signal,
  );
}

/** Categoría RECOMENDADA. Es una sugerencia: no se guarda hasta que alguien la acepta. */
export interface SugerenciaCategoria extends CategoriaTikTok {
  origen: string | null;      // "recomendador de TikTok" | "IA sobre categorías reales"
  confianza: number | null;
  motivo: string | null;
}

export function sugerirCategoriaTikTok(
  sku: string,
  titulo?: string,
  signal?: AbortSignal,
): Promise<SugerenciaCategoria> {
  const q = titulo ? `?titulo=${encodeURIComponent(titulo)}` : "";
  return getJSON(
    `/api/productos/${encodeURIComponent(sku)}/canal/tiktok/categoria/sugerida${q}`,
    signal,
  );
}

export function guardarCategoriaTikTok(
  sku: string,
  categoriaId: string,
): Promise<{ ok: boolean; categoria_id: string; nombre?: string; ruta?: string }> {
  return postJSON(
    `/api/productos/${encodeURIComponent(sku)}/canal/tiktok/categoria`,
    { categoria_id: categoriaId },
  );
}

/* ── Categoría de TEMU ───────────────────────────────────────────────────────
 * Mismo contrato que TikTok. La diferencia de fondo: en Temu la categoría
 * DETERMINA qué atributos existen (`template.get` solo responde en hojas), así
 * que sin elegirla no hay contenido que generar ni alta que mandar.
 */

export function buscarCategoriasTemu(
  q: string,
  signal?: AbortSignal,
): Promise<{ canal: string; resultados: CategoriaTikTok[] }> {
  return getJSON(`/api/productos/categorias/temu?q=${encodeURIComponent(q)}`, signal);
}

export function categoriaTemuActual(
  sku: string,
  signal?: AbortSignal,
): Promise<CategoriaTikTok & { origen: string | null }> {
  return getJSON(
    `/api/productos/${encodeURIComponent(sku)}/canal/temu/categoria`,
    signal,
  );
}

/** Candidatas de Temu + la que la IA eligió, o `ninguna: true` si no encaja. */
export interface SugerenciaTemu {
  ok: boolean;
  motivo?: string | null;
  sugerida?: { category_id: string; name?: string | null; path?: string | null } | null;
  razon?: string | null;
  ninguna?: boolean;
  candidatas?: { categoria_id: string; path: string }[];
}

export function sugerirCategoriaTemu(
  sku: string,
  titulo?: string,
  signal?: AbortSignal,
): Promise<SugerenciaTemu> {
  const q = titulo ? `?titulo=${encodeURIComponent(titulo)}` : "";
  return getJSON(
    `/api/productos/${encodeURIComponent(sku)}/canal/temu/categoria/sugerida${q}`,
    signal,
  );
}

export function guardarCategoriaTemu(
  sku: string,
  categoriaId: string,
): Promise<{ ok: boolean; categoria_id: string; nombre?: string; path?: string }> {
  return postJSON(
    `/api/productos/${encodeURIComponent(sku)}/canal/temu/categoria`,
    { categoria_id: categoriaId },
  );
}

// ── Competencia (Mercado Libre) ──────────────────────────────────────
// Los GET leen la foto guardada del mes (SQLite local). La corrida real la
// dispara el cron mensual de Railway; correrCompetencia() es para probar a mano
// y gasta Apify (~$1 USD por SKU).

export function estadoCompetencia(signal?: AbortSignal) {
  return getJSON<CompetenciaEstado>("/api/competencia/estado", signal);
}

/**
 * Tabla por SKU. `agrupar` por defecto es la categoría RAÍZ del path
 * ("Accesorios para Vehículos"); `categoria_nombre` agrupa por la última.
 * `canal` separa Mercado Libre de Amazon.
 */
export function tablaCompetencia(
  agrupar = "raiz_nombre",
  canal = "mercado_libre",
  signal?: AbortSignal,
) {
  return getJSON<CompetenciaTabla>(
    `/api/competencia/tabla?agrupar=${agrupar}&canal=${canal}`,
    signal,
  );
}

/** Top de más vendidos de una categoría. `nivel` es 'raiz' u 'hoja'. */
/** El árbol completo del tab: raíz → subcategorías → nuestros SKUs, de un GET. */
export function vistaCompetencia(canal = "mercado_libre", signal?: AbortSignal) {
  return getJSON<CompetenciaVista>(`/api/competencia/vista?canal=${canal}`, signal);
}

/** Lo que se abre al hacer clic en un SKU: términos populares + competencia directa. */
export function detalleSkuCompetencia(sku: string, signal?: AbortSignal) {
  return getJSON<CompetenciaDetalleSku>(
    `/api/competencia/sku/${encodeURIComponent(sku)}`,
    signal,
  );
}

/** Todos nuestros SKUs de una categoría con su barra por tienda. */
export function skusSubcategoria(categoriaId: string, signal?: AbortSignal) {
  return getJSON<CompetenciaSkusSub>(
    `/api/competencia/subcategoria/${encodeURIComponent(categoriaId)}/skus`,
    signal,
  );
}

/** Barra de términos de una subcategoría: qué se busca ahí y qué cubrimos. */
export function terminosSubcategoria(categoriaId: string, signal?: AbortSignal) {
  return getJSON<CompetenciaTerminosSub>(
    `/api/competencia/subcategoria/${encodeURIComponent(categoriaId)}/terminos`,
    signal,
  );
}

/** Palabras clave que la IA sugiere para toda la subcategoría. */
export function sugerirSubcategoria(categoriaId: string) {
  return postJSON<CompetenciaSugerenciaSub>(
    `/api/competencia/subcategoria/${encodeURIComponent(categoriaId)}/sugerir`,
    {},
  );
}

/** Palabras y títulos que la IA sugiere para un SKU, por tienda. */
export function sugerirSku(sku: string) {
  return postJSON<CompetenciaSugerenciaSku>(
    `/api/competencia/sku/${encodeURIComponent(sku)}/sugerir`,
    {},
  );
}

export function rankingCategoria(
  categoriaId: string,
  nivel?: "raiz" | "hoja",
  limite = 10,
  signal?: AbortSignal,
) {
  const q = nivel ? `&nivel=${nivel}` : "";
  return getJSON<RankingCategoriaResp>(
    `/api/competencia/ranking-categoria?categoria_id=${encodeURIComponent(categoriaId)}${q}&limite=${limite}`,
    signal,
  );
}

/** Raspa los más vendidos de la raíz y la hoja de cada SKU vigilado. */
export function capturarRankingsCompetencia() {
  return postJSON<{
    ok: boolean;
    categorias: number;
    con_datos: number;
    avisos: string[];
  }>("/api/competencia/rankings", {});
}

// detalleCompetencia PODADA (paso 6): GET /detalle se retiró del backend y
// esta función no tenía ni un solo consumidor en la UI.

// topCategoriaCompetencia PODADA (paso 6): exportada y jamás llamada.
// GET /top-categoria sigue vivo en el backend por el modo local.

export function skusCompetencia(signal?: AbortSignal) {
  return getJSON<{ skus: CompetenciaSku[] }>("/api/competencia/skus", signal);
}

export function sembrarCompetencia(skus: string[], con_ia = true) {
  return postJSON<{ ok: boolean; guardados: number }>(
    "/api/competencia/sembrar",
    { skus, con_ia },
  );
}

/** Corrige el término general; queda 'manual' y la IA no lo vuelve a pisar. */
export async function corregirTerminoCompetencia(sku: string, termino_general: string) {
  const res = await fetch(`${BASE}/api/competencia/skus/${encodeURIComponent(sku)}`, {
    method: "PATCH",
    headers: cabeceras({ "Content-Type": "application/json" }),
    body: JSON.stringify({ termino_general }),
  });
  if (!res.ok) throw new Error(`API ${res.status}: corregir término`);
  return (await res.json()) as { ok: boolean; sku: string; termino_general: string };
}

export function correrCompetencia(skus?: string) {
  const q = skus ? `?skus=${encodeURIComponent(skus)}` : "";
  return postJSON<{ ok: boolean; estado: string }>(`/api/competencia/correr${q}`, {});
}

/** Refresca las visitas de NUESTRAS publicaciones (API de ML, gratis). */
export function refrescarVisitasPropias(skus?: string) {
  const q = skus ? `?skus=${encodeURIComponent(skus)}` : "";
  return postJSON<{
    ok: boolean;
    skus: number;
    publicaciones: number;
    con_visitas: number;
    sin_dato: string[];
  }>(`/api/competencia/visitas-propias${q}`, {});
}

export function corridaCompetencia(signal?: AbortSignal) {
  return getJSON<{
    ultima: CompetenciaCorrida | null;
    en_curso: { estado: string; error?: string | null };
  }>("/api/competencia/corrida", signal);
}

// ── Resolver de costos (packing list vs costos_validados) ────────────
// Herramienta de /costos. El análisis corre en background: estas llamadas
// arrancan el trabajo y la UI hace polling a estadoResolver.
//
// Todas pasan por fetchSesion: un fetch pelón NO manda el token y devuelve 401
// con el enforcement encendido.

export async function analizarPackingArchivo(
  archivo: File,
  opts: { costoContenedor?: number; tipoCambio?: number; contenedor?: string } = {},
): Promise<{ id: string; paso: string; paso_label: string }> {
  const fd = new FormData();
  fd.append("archivo", archivo);
  if (opts.contenedor) fd.append("contenedor", opts.contenedor);
  if (opts.costoContenedor != null)
    fd.append("costo_contenedor", String(opts.costoContenedor));
  if (opts.tipoCambio != null) fd.append("tipo_cambio", String(opts.tipoCambio));

  // Sin Content-Type a mano: el navegador tiene que poner el boundary del multipart.
  const res = await fetchSesion(`${BASE}/api/resolver/analizar`, {
    method: "POST",
    body: fd,
  });
  if (!res.ok) throw await errorDeRespuesta(res, "/api/resolver/analizar");
  return res.json();
}

export function analizarPackingUrl(
  url: string,
  opts: { costoContenedor?: number; tipoCambio?: number; contenedor?: string } = {},
): Promise<{ id: string; paso: string; paso_label: string }> {
  return postJSON("/api/resolver/analizar-url", {
    url,
    contenedor: opts.contenedor,
    costo_contenedor: opts.costoContenedor,
    tipo_cambio: opts.tipoCambio,
  });
}

export function estadoResolver(
  id: string,
  signal?: AbortSignal,
): Promise<ResolverEstado> {
  return getJSON<ResolverEstado>(`/api/resolver/${encodeURIComponent(id)}`, signal);
}

/** Corrige a mano el SKU de un renglón; el backend recalcula SU comparación. */
export async function corregirEmpateResolver(
  id: string,
  indice: number,
  sku: string | null,
): Promise<ResolverFila> {
  const path = `/api/resolver/${encodeURIComponent(id)}/empate`;
  const res = await fetchSesion(
    `${BASE}${path}`,
    { method: "PATCH", body: JSON.stringify({ indice, sku }) },
    { "Content-Type": "application/json" },
  );
  if (!res.ok) throw await errorDeRespuesta(res, path);
  return res.json();
}

/**
 * Captura datos de un renglón (cajas, piezas por caja, total, dims de caja…) y
 * el backend deriva el resto hasta dimensiones y peso por pieza. Recalcula el
 * contenedor completo porque el flete se prorratea sobre el CBM total.
 */
export async function capturarFilaResolver(
  id: string,
  indice: number,
  campos: Record<string, number>,
): Promise<{ fila: ResolverFila; totales: ResolverTotales }> {
  const path = `/api/resolver/${encodeURIComponent(id)}/fila`;
  const res = await fetchSesion(
    `${BASE}${path}`,
    { method: "PATCH", body: JSON.stringify({ indice, ...campos }) },
    { "Content-Type": "application/json" },
  );
  if (!res.ok) throw await errorDeRespuesta(res, path);
  return res.json();
}

/**
 * Busca un SKU en TODO el catálogo (no solo en los candidatos del contenedor).
 * Cada resultado dice con qué contenedor está capturado y en qué renglones de
 * este análisis ya se usó.
 */
export function buscarSkuResolver(
  id: string,
  q: string,
  signal?: AbortSignal,
): Promise<{ resultados: ResolverSkuBuscado[] }> {
  return getJSON(
    `/api/resolver/${encodeURIComponent(id)}/buscar-sku?q=${encodeURIComponent(q)}`,
    signal,
  );
}

/** UPSERT en costos_validados. `skus` acota qué se escribe. */
export function guardarResolver(
  id: string,
  skus?: string[],
  editados?: ResolverEdicion[],
): Promise<ResolverGuardado> {
  return postJSON(`/api/resolver/${encodeURIComponent(id)}/guardar`, {
    skus,
    editados,
  });
}
