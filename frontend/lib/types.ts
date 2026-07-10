// Tipos que reflejan el contrato del backend FastAPI (models/schemas.py).

export interface CategoriaNivel {
  id: string | number | null;
  nombre: string;
}

export interface CanalResumen {
  canal: string;
  publicado: boolean;
  item_id: string | null;
  url: string | null;
}

export interface VarianteResumen {
  sku: string;
  nombre: string | null; // opciones de atributos ("Café / XL")
  precio: number | null;
  costo: number | null; // costo_unitario de costos_finales
  stock: number | null;
  valor: number | null; // stock × costo
  estado: string | null;
  contenedor: string | null; // nº de contenedor (costos_validados)
  // Presencia de ESTA variante en cada marketplace (Productos / Omnicanal).
  canales?: CanalResumen[];
}

export interface Producto {
  sku: string;
  wc_id: number | null;
  odoo_id: number | null;
  nombre: string;
  imagen: string | null;
  marca: string | null;
  descripcion_corta: string | null;
  precio: number | null;
  precio_base: number | null;
  moneda: string;
  stock: number | null;
  stock_real: number | null;
  stock_full: number | null;
  stock_fba: number | null;
  situacion: string | null;
  estado: string | null;
  categoria_path: CategoriaNivel[];
  categoria_id: string | number | null;
  full: boolean | null;
  full_label: string | null;
  publicado: boolean;
  item_id: string | null;
  url: string | null;
  canales: CanalResumen[];
  cuenta: string | null;
  // Valor de inventario (Crear Productos): costo y valor = stock × costo
  costo: number | null;
  valor: number | null;
  contenedor: string | null; // nº de contenedor (costos_validados)
  // Tipo en WooCommerce: simple | variable (padre) | variation
  tipo: string | null;
  // Si es padre: sus variantes (vista Crear Productos)
  variantes: VarianteResumen[];
  origen: string;
}

export interface Paginacion {
  page: number;
  per_page: number;
  total: number;
  total_pages: number;
  tiene_anterior: boolean;
  tiene_siguiente: boolean;
}

export interface RespuestaProductosBase {
  // false mientras el índice se construye (carga progresiva)
  completo?: boolean;
}

export interface RespuestaProductos extends RespuestaProductosBase {
  canal: string;
  items: Producto[];
  paginacion: Paginacion;
}

export interface SubCuentaInfo {
  id: string;
  label: string;
  es_default: boolean;
  total_productos: number | null;
}

export interface CanalInfo {
  id: string;
  label: string;
  color: string;
  color_texto: string;
  acento: string;
  habilitado: boolean;
  origen: string;
  descripcion: string;
  total_productos: number | null;
  subcuentas: SubCuentaInfo[];
}

export interface DetalleCanal {
  canal: string;
  publicado: boolean;
  item_id: string | null;
  url: string | null;
  precio: number | null;
  precio_base: number | null;
  stock: number | null;
  stock_real: number | null;
  stock_full: number | null;
  stock_fba: number | null;
  situacion: string | null;
  full: boolean | null;
  full_label: string | null;
  categoria_id: string | number | null;
  categoria_path: CategoriaNivel[];
  estado: string | null;
  extra: Record<string, unknown>;
}

export interface WebhookEvento {
  id: number;
  canal: string;
  topic: string | null;
  resource: string | null;
  cuenta: string | null;
  sku: string | null;
  resultado: string | null;
  recibido: string;
}

export interface AtributoProducto {
  nombre: string;
  valor: string;
}

export interface DetalleProducto {
  sku: string;
  wc_id: number | null;
  odoo_id: number | null;
  nombre: string;
  imagen: string | null;
  imagenes: string[];
  marca: string | null;
  descripcion: string | null;
  descripcion_corta: string | null;
  atributos: AtributoProducto[];
  precio_base: number | null;
  precio_oferta: number | null;
  stock_odoo: number | null;
  costo: number | null;
  peso_kg: number | null;
  dimensiones: string | null;
  canales: DetalleCanal[];
}

// ── Editor de imágenes (galería WooCommerce + IA por flags) ──────────
export interface GaleriaImagen {
  id: number;
  src: string;
  position: number;
}

export interface FlagsImagen {
  quitar_fondo: boolean;
  traducir_texto: boolean;
  cambiar_modelo: boolean;
}

export type EstadoImagen =
  | "pendiente"
  | "procesando"
  | "listo"
  | "error"
  | "sin_flags";

export interface ImagenProgreso {
  indice: number;
  wc_image_id: number | null;
  src: string;
  estado: EstadoImagen;
  paso: string;
  error: string | null;
  nueva_url: string | null;
  nuevo_id: number | null;
  flags: FlagsImagen;
}

export interface ProgresoImagenes {
  sku: string;
  wc_id: number | null;
  estado: "procesando" | "completado" | "sin_datos";
  total: number;
  procesadas: number;
  paso_global: string;
  imagenes: ImagenProgreso[];
}

export interface GaleriaResp {
  sku: string;
  wc_id: number | null;
  parent_id: number | null;
  es_variacion?: boolean;
  portada: GaleriaImagen | null;
  imagenes: GaleriaImagen[];
  progreso?: ProgresoImagenes | null;
}

// ── IA: generadores de contenido por canal ──────────────────────────
export interface GeneradorDef {
  id: string;
  label: string;
  icono: string;
  descripcion: string;
  tipo?: "texto" | "imagenes";
  max_tokens?: number;
}

// ── Estudio de producto: metadata completa (postmeta / kubera_ml) ────
export interface StudioDinero {
  costo: number | null;
  precio_regular: number | null;
  precio_oferta: number | null;
  peso: number | null;
  largo: number | null;
  ancho: number | null;
  alto: number | null;
  volumen_m3: number | null;
}

export interface StudioCategoriaML {
  category_id: string | null;
  ruta: string | null;
  niveles: string[];
}

export interface EstadoPublicacion {
  ml: { cuenta: string; item_id: string; fuente?: string }[];
  amazon: { publicado: boolean; asin: string | null; status: string | null; fuente?: string };
}

export interface StudioMetadata {
  sku: string;
  wc_id: number | null;
  fuente: string | null; // "postmeta" | "kubera_ml" | null
  dinero: StudioDinero;
  stock: number | null;
  categoria_ml: StudioCategoriaML | null;
  alibaba_url: string | null;
  alibaba_precio: number | null;
  producto_correcto: string | null;
  atributos: AtributoProducto[];
  estado?: EstadoPublicacion;
}

// ── Costos: desglose + recálculo (tab COSTOS) ────────────────────────
export interface CostoCalculo {
  sku: string;
  costo_producto: number | null;
  costo_cbm: number | null;
  costo_unitario: number | null;
  largo: number | null;
  alto: number | null;
  ancho: number | null;
  peso: number | null;
  volumen_m3: number | null;
  ml_cat_id: string | null;
  margen: number;
  incluir_envio: boolean;
  tarifa_cbm_m3: number;
  pct_comision: number;
  comision_estimada?: boolean; // true si la comisión salió del fallback (sin token/categoría)
  costo_comision: number;
  costo_fee_envio: number;
  iva_mnt: number;
  precio_sugerido: number;
  precio_base: number;
  ganancia_neta: number;
  roi: number;
}

export interface CostoDetalle {
  sku: string;
  finales: Record<string, unknown> | null;
  validados: Record<string, unknown> | null;
  logs: { accion: string; origen: string; created_at: string }[];
  constantes: { margen: number; iva: number; descuento: number };
}

export interface CostoPreviewResp {
  ok: boolean;
  sku: string;
  calculo: CostoCalculo;
}

export interface CostoGuardarResp {
  ok: boolean;
  sku: string;
  finales: Record<string, unknown>;
  sincronizado_woo: boolean;
}

export interface CostoRow {
  sku: string;
  nombre: string | null;
  contenedor: string | null;
  largo: number | null;
  ancho: number | null;
  alto: number | null;
  peso: number | null;
  volumen_m3: number | null;
  costo_producto: number | null;
  costo_cbm: number | null;
  costo_unitario: number | null;
  precio_base: number | null;
  precio_sugerido: number | null;
  ml_cat_id: string | null;
}

export interface CostosListResp {
  items: CostoRow[];
  paginacion: Paginacion;
}

export interface ContenedorInfo {
  contenedor: string;
  n: number;
}

export interface CostoBulkItem {
  sku: string;
  costo_producto?: number | null;
  largo?: number | null;
  alto?: number | null;
  ancho?: number | null;
  peso?: number | null;
}

export interface CostoBulkResultado {
  sku: string;
  ok: boolean;
  error?: string;
  aviso?: string;
  sincronizado_woo?: boolean;
  costo_unitario?: number | null;
  precio_base?: number | null;
  precio_sugerido?: number | null;
  costo_cbm?: number | null;
}

export interface CostoBulkResp {
  ok: boolean;
  total: number;
  exitosos: number;
  resultados: CostoBulkResultado[];
}

export interface CategoriaMLResult {
  category_id: string;
  name: string;
  path: string;
  domain: string;
}

export interface CostoOverrides {
  costo_producto?: number | null;
  costo_cbm?: number | null;
  largo?: number | null;
  alto?: number | null;
  ancho?: number | null;
  peso?: number | null;
  ml_cat_id?: string | null;
  pct_comision?: number | null;
  incluir_envio?: boolean;
  margen?: number;
  auto_cbm?: boolean;
  sincronizar_woo?: boolean;
}

// ── Mejorar con IA (un botón por canal) ──────────────────────────────
export interface MejorarCampos {
  titulo?: string;
  descripcion?: string;
  highlights?: string;
  bullets?: string[];
  atributos?: AtributoProducto[];
}

export interface MejorarResp {
  ok: boolean;
  canal: string;
  proveedor?: string;
  motivo?: string;
  campos?: MejorarCampos;
}

// ── Precio de competencia sugerido ───────────────────────────────────
export interface CompetenciaFuente {
  marketplace: string;
  titulo: string | null;
  precio: number | null;
  url: string | null;
}

export interface CompetenciaPorMarketplace {
  marketplace: string;
  min?: number;
  max?: number;
  n?: number;
  estimado_min?: number;
  estimado_max?: number;
}

export interface CompetenciaResp {
  ok: boolean;
  motivo?: string;
  proveedor?: string;
  con_lista?: boolean;
  query?: string;
  precio_sugerido?: number | null;
  moneda?: string;
  rango?: { min: number; max: number; mediana: number } | null;
  por_marketplace?: CompetenciaPorMarketplace[];
  razonamiento?: string;
  aviso?: string;
  fuentes?: CompetenciaFuente[];
  fuentes_encontradas?: number;
}

// ── Publicar / actualizar en el canal (paso 4) ───────────────────────
export interface PublicarReq {
  canal: string;
  cuenta?: string | null;
  sku?: string | null;
  wc_id?: number | null;
  item_id?: string | null;
  campos: {
    titulo?: string;
    descripcion?: string;
    highlights?: string;
    bullets?: string[];
    atributos?: { nombre: string; valor: string }[];
    precio_regular?: number | null;
    peso?: number | null;
    largo?: number | null;
    ancho?: number | null;
    alto?: number | null;
  };
}

export interface PublicarPreview {
  ok: boolean;
  motivo?: string;
  canal: string;
  cuenta?: string | null;
  item_id?: string | null;
  sku?: string | null;
  product_type?: string | null;
  cuentas?: string[];
  titulo?: string | null;
  descripcion?: string | null;
  cambios?: { etiqueta: string; valor: string }[];
  operaciones?: Record<string, number | boolean>;
  // Solo en modo "crear" (ML): payload exacto de POST /items que arma publisher_core.
  modo?: string;
  payload?: Record<string, unknown> | null;
  avisos?: string[];
}

export interface PublicarResultadoCuenta {
  cuenta: string;
  item_id: string;
  ok: boolean;
  error?: string | null;
  ml_status?: number | null;
  // ML ignora el `status: paused` del POST: el backend verifica y reintenta.
  pausado?: boolean;
  estado_ml?: string | null;
  aviso?: string;
}

export interface PublicarResultado {
  ok: boolean;
  motivo?: string;
  canal?: string;
  modo?: string; // "crear" | "actualizar"
  item_id?: string | null;
  ml_status?: number | null;
  desc_status?: number | null;
  status?: string | null;
  issue_count?: number;
  error?: string | null;
  respuesta?: unknown;
  resultados?: PublicarResultadoCuenta[];
  registrado_en?: string;
}

export interface GenerarIAResp {
  ok: boolean;
  texto?: string;
  modelo?: string;
  proveedor?: string;
  motivo?: string;
  canal: string;
  generador: string;
  label: string;
  tipo: "texto" | "imagenes";
}
