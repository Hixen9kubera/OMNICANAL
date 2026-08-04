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
  // false = no tiene costo capturado y está mostrando el del padre (heredado)
  costo_propio?: boolean;
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
  precio_base: number | null; // precio regular
  precio_oferta: number | null; // precio de descuento (_sale_price)
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
  quitar_logos: boolean;
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
  gtin?: string | null;
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
  // Motivo por el que Woo no se actualizó, cuando el costo SÍ quedó guardado.
  sync_error?: string | null;
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
  // Precio fijado a mano en el Estudio: manda sobre el derivado del costo.
  precio_base?: number | null;      // "Precio regular" (el que publica ML)
  precio_sugerido?: number | null;  // "Precio oferta"
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
  // Por fila: "crear" cuando la publicación anterior fue eliminada en ML y se
  // re-creó solo en esa cuenta (el modo global puede ser "actualizar").
  modo?: string;
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

/* ── Tab VENTAS ──────────────────────────────────────────────────── */

export interface VentaHora {
  hora: number;
  monto: number;
  pedidos: number;
  unidades: number;
  prev_monto: number;
  prev_pedidos: number;
  prev_unidades: number;
  delta_monto: number | null;
}

export interface VentasParcial {
  hora_corte: number;
  prev_monto: number;
  prev_pedidos: number;
  prev_unidades: number;
  delta: { monto: number | null; pedidos: number | null; unidades: number | null };
}

export interface VentasTotales {
  monto: number;
  pedidos: number;
  unidades: number;
  ticket: number;
  canceladas: number;
  monto_cancelado: number;
  prev: { monto: number; pedidos: number; unidades: number; ticket: number; canceladas: number };
  delta: { monto: number | null; pedidos: number | null; unidades: number | null; ticket: number | null };
  parcial: VentasParcial | null;
}

export interface VentasCuenta {
  cuenta: string;
  label: string;
  monto: number;
  pedidos: number;
  unidades: number;
  prev_monto: number;
  delta_monto: number | null;
  /** Solo cuando el rango es HOY: comparativa a la misma hora. */
  prev_monto_parcial?: number;
  delta_parcial?: number | null;
}

export interface PedidosWCResumen {
  total: number;
  monto: number;
  full: number;
  propios: number;
  cancelados: number;
  cuentas: Record<string, { pedidos: number; monto: number }>;
}

export interface VentasResumen {
  /** "pedidos" = alimentado por pedidos de WooCommerce (registro vivo). */
  fuente?: string;
  canal: string;
  cuenta: string | null;
  desde: string;
  hasta: string;
  prev_desde: string;
  prev_hasta: string;
  horas: VentaHora[];
  totales: VentasTotales;
  cuentas: VentasCuenta[];
  /** Pedidos ML→WC creados en el rango (registro vivo desde 2026-07-17). */
  pedidos_wc?: PedidosWCResumen | null;
  actualizado: string;
}

// ── Competencia (Mercado Libre) ───────────────────────────────────────
// Tres mediciones por SKU, que son las tres preguntas del tab:
//   'general'   → ¿me encuentran cuando buscan el TIPO de producto? (descubrimiento)
//   'titulo'    → ¿dónde quedo contra el mismo producto? (competencia directa)
//   'categoria' → ¿quiénes son los más vendidos de mi categoría? (ranking oficial)
//
// Límites reales, que la UI muestra en vez de esconder:
//   • `descripcion` es la CORTA derivada de atributos ("Largo: 4 m | Ancho: 6 m").
//     ML no expone el texto largo de publicaciones ajenas.
//   • `visitas_30d` viene de la API de ML y sí funciona para items ajenos.
//   • `vendidos` viene del scraper: la API no da sold_quantity de terceros.
//   • Un SKU sin publicar en ML sale siempre "fuera": no hay nada nuestro que
//     encontrar en el ranking. Es información, no un error.
//   • No hay histórico: cada corrida borra la anterior y reescribe.

export type TipoCompetencia = "general" | "titulo" | "categoria";
/** Niveles por los que se puede agrupar la tabla. `raiz_nombre` es la categoría
 *  PRINCIPAL (primer nivel del path) y `categoria_nombre` la ÚLTIMA. */
export type NivelCategoria =
  | "raiz_nombre"
  | "categoria_nombre"
  | "cat2"
  | "cat3"
  | "cat4";

export interface CompetenciaSku {
  sku: string;
  nombre: string;
  origen_nombre: string | null;      // 'productos' | 'woocommerce'
  /** La ÚLTIMA categoría (la real de la publicación). */
  categoria_id: string | null;
  categoria_nombre: string | null;
  /** La categoría PRINCIPAL: primer nivel del path. Su id sale de
   *  /categories/{id} path_from_root — `categorias_ml` guarda los nombres de
   *  cat1..cat4 pero no sus ids, y sin id no se puede pedir el ranking. */
  raiz_id: string | null;            // p.ej. MLM1747 = Accesorios para Vehículos
  raiz_nombre: string | null;
  ruta: string | null;               // 'Nivel 1 › Nivel 2 › … › Hoja'  (separador U+203A)
  cat1: string | null;
  cat2: string | null;
  cat3: string | null;
  cat4: string | null;
  ml_item_id: string | null;
  cuenta: string | null;
  publicado_ml: number;              // 0 | 1
  termino_general: string | null;
  termino_origen: "ia" | "manual";
  activo: number;
}

export interface CompetenciaResultado {
  id: number;
  sku: string;
  tipo: TipoCompetencia;
  termino: string | null;
  periodo: string;
  posicion: number | null;
  externo_id: string;
  titulo: string | null;
  descripcion: string | null;
  precio: number | null;
  moneda: string;
  imagen: string | null;
  url: string | null;
  seller: string | null;
  marca: string | null;
  visitas_30d: number | null;
  vendidos: number | null;
  reviews: number | null;
  rating: number | null;
  envio_gratis: number | null;
  es_full: number | null;
  es_nuestro: number;
  sku_nuestro: string | null;
}

export interface CompetenciaPosicion {
  sku: string;
  tipo: TipoCompetencia;
  termino: string | null;
  periodo: string | null;
  total_resultados: number;
  mi_posicion: number | null;
  mi_precio: number | null;
  mis_visitas_30d: number | null;
  mis_vendidos: number | null;
  precio_mediana_rivales: number | null;
  visitas_max_rival: number | null;
}

/** Una PUBLICACIÓN nuestra. `canal` está desde el inicio para que un ASIN de
 *  Amazon sea otra fila y no un rediseño de la vista. */
export interface PublicacionPropia {
  cuenta: string;                    // BEKURA | SANCORFASHION
  canal: string;                     // mercado_libre | amazon
  ml_item_id: string;                // MLM… | ASIN
  /** El título es POR TIENDA y suele diferir entre BEKURA y SANCORFASHION. */
  titulo: string | null;
  url: string | null;
  imagen: string | null;
  precio: number | null;             // de channel.listings.price en producción
  estado: string | null;
  visitas_30d: number | null;        // API de ML
  unidades_30d: number | null;       // de los pedidos
  /** unidades/visitas. null si no hay visitas: con 0 visitas es INDEFINIDA, no 0%. */
  conversion_30d: number | null;
}

/** Una fila de la tabla: un SKU con mi posición en las tres mediciones. */
export interface CompetenciaFilaSku extends CompetenciaSku {
  /** Una entrada por publicación (dos por SKU: una por tienda). */
  tiendas: PublicacionPropia[];
  /** Foto del CATÁLOGO (WooCommerce), no la de la publicación de ML. */
  imagen: string | null;
  visitas_30d: number | null;        // suma de las tiendas
  unidades_30d: number | null;
  conversion_30d: number | null;
  actualizado: string | null;
  pos_gen: number | null;
  total_gen: number | null;
  pos_tit: number | null;
  total_tit: number | null;
  pos_cat: number | null;
  total_cat: number | null;
  periodo: string | null;
}

export interface CompetenciaGrupo {
  grupo: string;
  nivel: NivelCategoria;
  /** Id de la categoría del grupo: permite pedir SU ranking de más vendidos. */
  categoria_id: string | null;
  skus: CompetenciaFilaSku[];
}

/** Una fila del top de más vendidos de una categoría (raspado de /mas-vendidos/). */
export interface RankingCategoria {
  categoria_id: string;
  nivel: "raiz" | "hoja";
  posicion: number;              // del badge oficial "1º MÁS VENDIDO"
  externo_id: string;
  titulo: string | null;
  precio: number | null;         // el que se paga (con descuento)
  precio_lista: number | null;   // precio base, antes del descuento
  descuento: string | null;      // '58% OFF'
  vendidos: number | null;       // cota inferior: ML redondea (+50mil → 50000)
  rating: number | null;         // 0-5
  seller: string | null;
  imagen: string | null;
  url: string | null;
  visitas_30d: number | null;
  reviews: number | null;
  /** El id que va en el URL: es el MISMO que devuelve /highlights. */
  id_pagina: string | null;
  tipo: "ITEM" | "PRODUCT" | "USER_PRODUCT" | null;
  /** Subcategoría de esta fila. Solo en el ranking de la raíz; null en los ITEM. */
  item_categoria_id: string | null;
  item_categoria_nombre: string | null;
  es_nuestro: number;
  sku_nuestro: string | null;
}

/** Un SKU nuestro con su posición frente al mercado de su subcategoría. */
export interface CompetenciaSkuVista extends CompetenciaFilaSku {
  /** El precio más bajo que realmente cobramos entre las tiendas. */
  precio_ref: number | null;
  mediana_mercado: number | null;
  /** precio_ref / mediana. 5.3 = cobramos 5.3 veces el precio del mercado. */
  brecha: number | null;
  /** Posición de SU subcategoría en el top de la categoría padre. */
  pos_en_raiz: number | null;
  /** Unidades del top de su subcategoría: el tamaño del nicho. */
  volumen_mercado: number | null;
  sin_datos_ml: boolean;
  n_terminos: number;
  /** La publicación PAUSADA tiene más tráfico que la activa. */
  pausada_es_la_que_vende: boolean;
  /** Aparecemos en el top de nuestra subcategoría, y en qué posición. */
  posicion_top: number | null;
  en_top: boolean;
}

export interface CompetenciaSubcategoria {
  categoria_id: string | null;
  categoria_nombre: string;
  ruta: string | null;
  n_skus: number;
  n_ranking: number;
  top: RankingCategoria[];
  skus: CompetenciaSkuVista[];
  mediana: number | null;
  precio_min: number | null;
  precio_max: number | null;
  volumen_mercado: number | null;
  n_terminos: number;
  /** ML no publica ni ranking ni términos de esta categoría. No es un fallo. */
  sin_datos_ml: boolean;
  pos_en_raiz: number | null;
}

export interface CompetenciaRaiz {
  raiz_id: string | null;
  raiz_nombre: string;
  n_skus: number;
  n_publicaciones: number;
  terminos_raiz: number;
  top: RankingCategoria[];
  /** Los 5 nichos del top del padre, en orden, con lo que tenemos en cada uno. */
  nichos: CompetenciaNicho[];
  /** Nuestros 5 con más chance, por posición de su subcategoría y por volumen. */
  oportunidad: CompetenciaSkuVista[];
  subcategorias: CompetenciaSubcategoria[];
  skus: CompetenciaSkuVista[];
}

/**
 * Un nicho del top de la categoría padre: el #1, el #2… con lo que tenemos ahí.
 * Los dicta el ranking de ML, no nuestro inventario, así que un nicho con
 * `n_catalogo: 0` es un hueco real (en MLM1747, el aceite de motor del #4).
 */
export interface CompetenciaNicho {
  categoria_id: string;
  categoria_nombre: string;
  posicion: number | null;
  /** Otras posiciones del top que caen en el mismo nicho (#5 y #6 se agrupan). */
  otras_posiciones: number[];
  lider: RankingCategoria;
  /** SKUs nuestros en esa categoría, de todo el catálogo (no solo los vigilados). */
  n_catalogo: number;
  skus_catalogo: string[];
  skus_vigilados: string[];
  tenemos: boolean;
  /** Hay producto nuestro pero nadie lo está midiendo. */
  sin_vigilancia: boolean;
}

export interface CompetenciaVista {
  canal: string;
  raices: CompetenciaRaiz[];
  /** Cuándo se raspó el ranking. Lo que importa en una vista de solo lectura. */
  capturado_en: string | null;
  /** Este servidor puede refrescar el ranking (tiene navegador). En Railway: no. */
  puede_refrescar: boolean;
  aviso: string | null;
}

/** Un SKU nuestro de una categoría, medido o solo publicado. */
export interface CompetenciaSkuDeCategoria {
  sku: string;
  nombre: string | null;
  imagen: string | null;
  /** Está bajo observación: su barra trae visitas, ventas y conversión. */
  vigilado: boolean;
  publicado: boolean;
  /** Si no está vigilado, visitas/ventas van en null — que NO es 0. */
  tiendas: PublicacionPropia[];
}

export interface CompetenciaSkusSub {
  categoria_id: string;
  skus: CompetenciaSkuDeCategoria[];
  n_total?: number;
  n_vigilados?: number;
  n_publicados?: number;
  aviso?: string;
  error?: string;
}

export interface CompetenciaTerminosSub {
  categoria_id: string;
  terminos: CompetenciaTermino[];
  total: number;
  cubiertos: number;
  skus: string[];
  aviso: string | null;
}

export interface CompetenciaPalabraSugerida {
  palabra: string;
  porque: string | null;
  variantes: string[];
  /** La palabra aparece en la demanda medida o en los títulos líderes. */
  respaldada: boolean;
}

export interface CompetenciaSugerenciaSub {
  ok: boolean;
  categoria_id: string;
  faltantes: string[];
  palabras: CompetenciaPalabraSugerida[];
  evitar: string[];
  /** Lo que la IA quiso evitar pero SÍ se busca: se descarta y se reporta. */
  evitar_descartados: string[];
}

/** UN título sugerido a partir de la competencia directa, máximo 60 caracteres. */
export interface CompetenciaSugerenciaSku {
  ok: boolean;
  sku: string;
  categoria_nombre: string | null;
  titulo: string;
  largo: number;
  excede_60: boolean;
  porque: string | null;
  /** Recalculado por el backend: lo que la IA presume cubrir no basta. */
  cubre_verificado: string[];
  cubre_declarado: string[];
  faltantes: string[];
  titulos_actuales: Record<string, string>;
  largos_actuales: Record<string, number>;
  lideres: string[];
}

/** Un término que la gente escribe en el buscador de ML, y si lo cubrimos. */
export interface CompetenciaTermino {
  categoria_id: string;
  posicion: number;
  termino: string;
  url: string | null;
  /** OR de las tiendas: ¿alguna nos hace encontrables? null si no hay título. */
  cubierto: boolean | null;
  /** Qué tiendas lo cubren. Los títulos difieren, así que la cobertura también. */
  cubierto_por: string[];
}

export interface CompetenciaDetalleSku {
  sku: string;
  nombre: string | null;
  imagen: string | null;
  categoria_id: string | null;
  categoria_nombre: string | null;
  ruta: string | null;
  termino_general: string | null;
  termino_origen: string | null;
  publicaciones: PublicacionPropia[];
  terminos: CompetenciaTermino[];
  terminos_total: number;
  terminos_cubiertos: number;
  top: RankingCategoria[];
  sin_datos_ml: boolean;
  aviso: string | null;
}

export interface RankingCategoriaResp {
  categoria_id: string;
  nivel: string | null;
  top: RankingCategoria[];
  aviso: string | null;
}

export interface CompetenciaCorrida {
  id: number;
  periodo: string;
  origen: string;
  estado: "corriendo" | "listo" | "error";
  skus_medidos: number;
  resultados: number;
  visitas_ok: number;
  costo_usd: number | null;
  error: string | null;
  avisos: string[];
  creado_en: string;
  terminado_en: string | null;
}

export interface CompetenciaTabla {
  agrupar: NivelCategoria;
  canal: string;
  niveles: NivelCategoria[];
  grupos: CompetenciaGrupo[];
  corrida: CompetenciaCorrida | null;
}

export interface CompetenciaDetalle {
  sku: string;
  posiciones: CompetenciaPosicion[];
  resultados: Partial<Record<TipoCompetencia, CompetenciaResultado[]>>;
}

export interface CompetenciaTopCategoria {
  sku: string;
  categoria_id: string | null;
  categoria_nombre: string | null;
  ruta: string | null;
  top: CompetenciaResultado[];
  aviso: string | null;
}

export interface CompetenciaEstado {
  supabase: boolean;
  /** selenium + beautifulsoup4 presentes: el raspado del ranking corre local. */
  navegador_local: boolean;
  scraper_apify: boolean;
  top_por_busqueda: number;
  con_detalle: boolean;
  costo_por_busqueda_usd: number;
  limites: Record<string, string>;
}
