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
  /* Marca de validación del costeo (0032). Ausente = pendiente: el backend
     solo manda estos campos cuando el SKU está validado. */
  revisado_at?: string | null;
  revisado_por?: string | null;
  revision_movida?: boolean;
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

/**
 * Qué pasó con `solo_activas` en ESTA petición. Llega sólo si se pidió el
 * filtro; `null`/ausente el resto del tiempo.
 *
 * LAS DOS LECTURAS NO SE PUEDEN CONFUNDIR — es la razón de que el bloque
 * exista, no un detalle de forma:
 *
 *   `aplicado: true` + `paginacion.total === 0`
 *     → el CERO ES LA RESPUESTA. Es el caso de TikTok hoy (sus 283 APPROVED
 *       están SELLER_DEACTIVATED). Se pinta "0 activas" + la `nota`, NUNCA
 *       "no encontrados" ni "preparando el catálogo".
 *
 *   `aplicado: false`
 *     → la lista NO está filtrada, aunque se haya pedido. Pasa en `general`
 *       (Woo es la fuente del catálogo, no un canal de venta) y pasaría en ML
 *       o Amazon si se apagara SUPABASE_READ_PUBLICACIONES. El chip no puede
 *       quedar encendido a secas: hay que decir la `nota`, o el usuario cree
 *       que ve activas y está viendo todo.
 *
 * Contrato cerrado por omni-backend (handoff 2026-08-25) ·
 * `backend/models/schemas.py::FiltroActivas`.
 */
export interface FiltroActivas {
  solo_activas: boolean;              // lo que se pidió
  aplicado: boolean;                  // si el canal pudo evaluar el criterio
  campo: "situacion" | "status" | null; // no es la misma columna en cada canal
  valores: string[];                  // los valores crudos que cuentan como activa
  nota: string | null;                // la trampa del canal, o el porqué de no aplicarlo
}

export interface RespuestaProductos extends RespuestaProductosBase {
  canal: string;
  items: Producto[];
  paginacion: Paginacion;
  filtro_activas?: FiltroActivas | null;
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
  // El COSTO se guardó pero no se pudo derivar el precio (casi siempre: el
  // producto todavía no tiene categoría ML, y no se inventa una comisión).
  sin_precio?: boolean;
  aviso?: string;
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
  // Marca de COSTO VALIDADO. El backend ya las devolvía (routers/crear.py) y el
  // tipo no las declaraba: el front iba atrás del contrato.
  revisado_at?: string | null;
  revisado_por?: string | null;
  revision_movida?: boolean;
  /**
   * ¿Tiene publicación viva en Mercado Libre? (`situacion in ('active','paused')`).
   *
   * `null` NO es "no está publicado": es "no se pudo saber" —el listado cayó al
   * fallback de MySQL, que está congelado desde el 13-ago y contestaría con la
   * foto de agosto—. Una tabla detenida contesta con seguridad lo que ya no sabe.
   */
  publicado_ml?: boolean | null;
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
  // Costo TOTAL a mano (campo "Costo" del Estudio): manda sobre producto+flete.
  costo_unitario?: number | null;
}

// ── Mejorar con IA (un botón por canal) ──────────────────────────────
export interface MejorarCampos {
  titulo?: string;
  descripcion?: string;
  highlights?: string;
  bullets?: string[];
  atributos?: AtributoProducto[];
  /** Amazon. Se mide en BYTES (249), no en caracteres: ver el contador del Estudio. */
  backend_search_terms?: string;
}

export interface MejorarResp {
  ok: boolean;
  canal: string;
  proveedor?: string;
  motivo?: string;
  campos?: MejorarCampos;
  /* ── Solo Amazon (v0.137.0) ─────────────────────────────────────────
   * El generador valida contra los límites reales del canal y contra los
   * requisitos de su productType. Lo que NO pasa no se aplica: llega aquí
   * para que se vea por qué, en vez de desaparecer. */
  /** Fatales: Amazon truncaría o ignoraría el campo. NO se aplican. */
  rechazados?: { campo: string; motivo: string }[];
  /** Publicable, pero fuera del estilo pedido. Sí se aplica. */
  avisos?: string[];
  /** Marcas registradas encontradas y qué se hizo con cada una. */
  terminos_detectados?: string[];
  product_type?: string | null;
  product_type_origen?: string;
  requisitos?: {
    estado: "ok" | "incompleto" | "sin_requisitos";
    obligatorios: number;
    cubiertos: string[];
    sin_cubrir: string[];
  };
  /** El documento quedó en enrich.channel_content (origen `ia`). */
  guardado?: { ok: boolean; campos?: number; motivo?: string } | null;
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

// TipoCompetencia PODADO (paso 6): era el eje del /detalle retirado.
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
  /** Categoría PADRE inmediata. El id sale de `channel.categories.parent_id`; el
   *  nombre del penúltimo segmento de `ruta`, porque channel.categories solo
   *  tiene las categorías HOJA y el join por parent_id daba NULL siempre. */
  padre_id: string | null;
  padre_nombre: string | null;
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

/** Una fila de resultados de búsqueda (enrich.market_search_results). Es el
 *  contrato REAL del backend tras el paso 5: los campos capturados-y-retirados
 *  (periodo, vendidos, visitas_30d, descuento, precio_lista…) ya no viajan. */
export interface CompetenciaResultado {
  termino: string | null;
  externo_id: string;
  posicion: number | null;
  titulo: string | null;
  precio: number | null;
  imagen: string | null;
  url: string | null;
  seller: string | null;
  rating: number | null;
  /** Visitas de 30 días de ESE resultado. La captura las pide por API (gratis);
   *  la 0017 quitó la columna por leerla como "vacía" y la 0018 la devolvió. */
  visitas_30d: number | null;
  es_nuestro: number;
  sku_nuestro: string | null;
}

// CompetenciaPosicion PODADA (paso 6): posiciones() leía el SQLite efímero
// (vacío en Railway) — pos_gen/pos_tit/pos_cat llevaban meses en None.

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
  /** El que el comprador PAGA (de /items/{id}/sale_price), no el de lista. */
  precio: number | null;
  /** El de LISTA. Puede ser muy superior: $7,756 contra $3,294 reales. */
  precio_lista: number | null;
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
  /** Cuándo se raspó esta fila. El backend ya lo mandaba (sale de
   *  `enrich.market_bestsellers`); el tipo no lo declaraba, así que el panel
   *  no podía decir qué tan vieja es la captura. */
  capturado_en: string | null;
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

/** Avisos que salen del sondeo GRATIS de /highlights, para cualquier categoría. */
export interface AvisosDelTop {
  /** ¿ML publica ranking ahí? `null` = todavía no se ha sondeado. */
  ml_publica?: boolean | null;
  /** Cuándo cambió el top de ML por última vez. */
  top_cambio_en?: string | null;
  /** ML se movió DESPUÉS de nuestra captura: lo que se ve ya no es lo de allá. */
  top_movido?: boolean | null;
}

export interface CompetenciaSubcategoria extends AvisosDelTop {
  categoria_id: string | null;
  categoria_nombre: string;
  ruta: string | null;
  /** De qué cuelga la subcategoría, con id para poder verificarlo en ML. Sale de
   *  `enrich.market_skus_v` (migración 0015): el id de
   *  `channel.categories.parent_id`, el nombre del penúltimo segmento de `ruta`. */
  padre_id: string | null;
  padre_nombre: string | null;
  n_skus: number;
  n_ranking: number;
  top: RankingCategoria[];
  skus: CompetenciaSkuVista[];
  mediana: number | null;
  precio_min: number | null;
  precio_max: number | null;
  volumen_mercado: number | null;
  /** Visitas de 30 días sumadas del top del nicho: el tamaño de la demanda. */
  visitas_mercado: number | null;
  n_terminos: number;
  /** No lo hemos capturado. NO significa que ML no lo tenga — la vista no puede
   *  distinguirlo, solo sabe lo que hay guardado. */
  sin_capturar: boolean;
  sin_datos_ml: boolean;
  pos_en_raiz: number | null;
}

export interface CompetenciaRaiz extends AvisosDelTop {
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
  /** Ficha del líder. Puede venir SIN título ni foto: la posición y la categoría
   *  son API, pero el título/foto/precio solo salen del raspado. */
  lider: Partial<RankingCategoria>;
  /** SKUs nuestros en esa categoría, de todo el catálogo (no solo los vigilados). */
  n_catalogo: number;
  skus_catalogo: string[];
  skus_vigilados: string[];
  tenemos: boolean;
  /** Hay producto nuestro PUBLICADO pero nadie lo está midiendo. */
  sin_vigilancia: boolean;
  /** SKUs de este nicho que tenemos en catálogo pero NO están publicados en ML. */
  n_sin_publicar?: number;
  /** `false` = `n_catalogo` es el CATÁLOGO entero: no se pudo filtrar por publicados. */
  solo_publicados?: boolean;
}

export interface CompetenciaVista {
  canal: string;
  raices: CompetenciaRaiz[];
  /** La captura MÁS RECIENTE del árbol. Optimista: no es la de la categoría que estás viendo. */
  capturado_en: string | null;
  /** La captura MÁS VIEJA del árbol. Es la que acota cuánto puede estar desfasado lo que ves. */
  capturado_desde: string | null;
  /**
   * Hasta qué DÍA llegan las ventas. Es COBERTURA, no frescura: sales_daily no
   * guarda cuándo se trajo el dato, solo el día que cubre. Llega sin hora
   * ("2026-09-01"), así que hay que parsearla como local — ver aFecha().
   */
  ventas_hasta: string | null;
  /** Cuándo se MIDIERON las visitas (timestamp real, no el último toque). */
  visitas_medidas: string | null;
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
  /** El CATÁLOGO entero de la categoría. `skus` solo trae los publicados. */
  n_total?: number;
  n_vigilados?: number;
  n_publicados?: number;
  /** Cuántos quedaron fuera de la lista por no estar publicados en ML. */
  n_sin_publicar?: number;
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
  /** Qué sale al buscar el TÉRMINO GENERAL. Se guarda POR TÉRMINO, así que los
   *  SKUs que comparten término comparten resultados — se mide y se paga una vez. */
  busqueda_general: CompetenciaResultado[];
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

// CompetenciaDetalle y CompetenciaTopCategoria PODADAS (paso 6): sus
// funciones de api.ts no tenían consumidor y GET /detalle se retiró.

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

// ── Resolver de costos (packing list vs costos_validados) ────────────
// Herramienta de un solo uso: el análisis vive 3 h en memoria del backend y no
// se persiste. Lo único que se escribe es el UPSERT que el usuario confirma.

export interface ResolverValores {
  costo_producto: number;
  costo_cbm: number;
  costo_total: number;
  costo_usd: number;
  // Por PIEZA — lo que se escribe en costos_validados
  largo: number;
  ancho: number;
  alto: number;
  peso: number;
  cbm_por_pieza?: number;
  // De la CAJA — lo que trae el packing list, capturable
  largo_caja?: number;
  ancho_caja?: number;
  alto_caja?: number;
  peso_caja?: number;
  cbm_caja?: number;
  cajas: number;
  piezas_por_caja: number;
  unidades: number;
}

/** Lo que hay hoy en costos_validados (sin costo_usd ni unidades). */
export type ResolverActual = Omit<ResolverValores, "costo_usd" | "unidades">;

export interface ResolverFila {
  fila: number | null;
  descripcion: string;
  producto_chn: string;
  /** Miniatura del Excel como data URI: no hay Storage en este flujo. */
  imagen: string | null;
  sku: string | null;
  sku_sugerido: string | null;
  confianza: "alta" | "media" | "baja" | string;
  razon_empate: string;
  /** nuevo = no empató con nada · igual = dentro del umbral · revisar = cambió demasiado */
  estado: "nuevo" | "igual" | "revisar";
  /** Diferencia relativa (0.35 = +35%). null si no hay con qué comparar. */
  diferencia: number | null;
  nuevo: ResolverValores;
  actual: ResolverActual | null;
  /** Qué le falta para poder guardarse (dimensiones de pieza, peso, unidades). */
  faltantes?: string[];
  /**
   * Campos capturados a mano en este renglón. El solucionador los trata como
   * DATO y ya no los sobreescribe; la tabla los resalta. Se leía con un cast
   * que evadía el tipo — ahora está declarado.
   */
  campos_editados?: string[];
}

export interface ResolverResumen {
  total: number;
  nuevos: number;
  revisar: number;
  iguales: number;
  candidatos: number;
  /** SKUs del contenedor que ningún renglón reclamó. */
  sin_empatar: string[];
}

export interface ResolverTotales {
  costo_contenedor: number;
  tipo_cambio: number;
  total_cbm: number;
  costo_por_m3: number;
  total_unidades: number;
  total_filas: number;
  costo_total: number;
}

/** SKU ya costeado del contenedor: alimenta el selector de empate. */
export interface ResolverCandidato {
  sku: string;
  nombre: string;
  /** Foto del producto en WooCommerce, para empatar viendo la imagen. */
  imagen?: string | null;
  costo_total: number;
  largo: number;
  ancho: number;
  alto: number;
  peso: number;
  cajas: number;
  piezas_por_caja: number;
}

/** Valores finales de un renglón; lo que no venga conserva lo calculado. */
export interface ResolverEdicion {
  indice: number;
  sku?: string | null;
  largo?: number;
  ancho?: number;
  alto?: number;
  peso?: number;
  costo_producto?: number;
  costo_cbm?: number;
  costo_total?: number;
  cajas?: number;
  piezas_por_caja?: number;
}

export interface ResolverEstado {
  id: string;
  archivo: string;
  paso: string;
  paso_label: string;
  actual: number;
  total: number;
  error?: string | null;
  contenedor?: string;
  /** El contenedor tal como está en costos_validados, con su sufijo " - NN". */
  contenedor_bd?: string;
  contenedores_encontrados?: { contenedor: string; n: number }[];
  candidatos?: ResolverCandidato[];
  totales?: ResolverTotales;
  avisos?: string[];
  comparacion?: { filas: ResolverFila[]; resumen: ResolverResumen };
  /** Prosa del agente: qué cambió y de qué desconfiar. */
  analisis?: string;
  /** Tabla en TSV para pegar en Excel. */
  tsv?: string;
}

/** Resultado de buscar un SKU en todo el catálogo, para empatar a mano. */
export interface ResolverSkuBuscado {
  sku: string;
  nombre: string;
  imagen?: string | null;
  /** Contenedor con el que está capturado hoy. Vacío = aún sin costo. */
  contenedor?: string | null;
  costo_total?: number;
  /** Índices de los renglones de ESTE análisis que ya lo usan. */
  usado_en_filas: number[];
}

export interface ResolverGuardado {
  escritos: number;
  saltados: { sku?: string; fila?: number; motivo: string }[];
  errores: { sku: string; error: string }[];
}

// ── Validar costo de PUBLICADOS EN MERCADO LIBRE (flujo SKU-primero) ──
//
// Es el Resolver al REVÉS. El de siempre es packing-list-primero: cargas un
// xlsx y empatas sus renglones contra los SKUs del contenedor. Este arranca de
// los SKUs —solo los que tienen publicación viva en Mercado Libre— y a cada uno
// le busca su renglón en el packing list que le toca.
//
// REGLA CRÍTICA DE NEGOCIO: el proceso aplica ÚNICAMENTE a productos publicados
// en Mercado Libre. El filtro de esta pantalla es comodidad; la regla la aplica
// el backend al arrancar el trabajo y OTRA VEZ antes de escribir. Un `curl` se
// salta cualquier filtro de pantalla.
//
// Como el Resolver viejo, el trabajo vive ~3 h en MEMORIA del backend y no se
// persiste. Lo único que se escribe es el guardado que el usuario confirma.

/** Por qué un SKU seleccionado quedó fuera del lote. */
export type MotivoOmitido =
  | "no_publicado_ml"
  | "sin_odoo"
  | "sin_contenedor"
  | "duplicado"
  | string;

export interface PublicadoOmitido {
  sku: string;
  motivo: MotivoOmitido;
  detalle: string;
}

/** Pronóstico de UN SKU antes de gastar un peso de IA. */
export interface PreflightElegible {
  sku: string;
  publicado_ml: boolean;
  /** "BEKURA" | "SANCORFASHION" — puede haber publicación en las dos cuentas. */
  cuentas: string[];
  /** "active" | "paused" | "under_review" */
  situaciones: string[];
  /** Un SKU padre nunca aparece en un packing list: se expande a sus variantes. */
  padre: boolean;
  variantes: string[];
  contenedor: string | null;
  fuente_contenedor: "kubera" | "odoo" | "ambas" | null;
  /** Odoo y kubera dicen contenedores distintos: desempata la imagen. */
  fuentes_en_desacuerdo: boolean;
  /** Cuántos packing lists de Drive se le encontraron a ese contenedor. */
  archivos: number;
  foto_odoo: boolean;
  /** Si no es null, ya tiene COSTO VALIDADO y el candado está puesto. */
  revisado_at: string | null;
}

export interface PreflightResumen {
  pedidos: number;
  elegibles: number;
  omitidos: number;
  expandidos: number;
  con_contenedor: number;
  sin_contenedor: number;
  con_foto_odoo: number;
  ya_validados: number;
  /** max(updated_at) de channel.listings en ML — si envejece, el filtro miente. */
  listings_ml_actualizado?: string | null;
}

export interface PreflightResp {
  elegibles: PreflightElegible[];
  omitidos: PublicadoOmitido[];
  resumen: PreflightResumen;
}

/** Peldaño que resolvió el empate (o por qué no se resolvió). */
export type EstadoPublicado =
  | "sha256"      // la foto de Odoo y la del packing list son el MISMO archivo
  | "dhash"       // se parecen lo suficiente (≤8/64) y con margen sobre el 2º
  | "ia"          // foto de la publicación de ML + veredicto de visión
  | "sin_match"   // nadie resolvió: lo decide un humano viendo las fotos
  | "sin_insumo"; // faltó el insumo (sin contenedor, sin foto, sin archivo)

export interface PublicadoCandidatoIA {
  fila: number;
  por_que: string;
  confianza: string;
}

export interface PublicadoVeredicto {
  fila: number;
  mismo_producto: boolean;
  confianza: string;
  por_que: string;
}

/** Una fila del resultado: un SKU con el renglón que se le encontró. */
export interface FilaPublicado {
  // identidad
  sku: string;
  padre: string | null;
  nombre: string | null;
  titulo_ml: string | null;
  item_id_ml: string | null;
  cuenta_ml: string | null;
  situacion_ml: string | null;

  // veredicto
  estado: EstadoPublicado;
  /** 0 = foto de Odoo · 1 = léxico (informativo, NO decide) · 2 = IA */
  peldano: number | null;
  detalle: string;
  confianza: "alta" | "media" | "baja" | null;
  lexico: number | null;

  // origen del renglón
  fuente: "kubera" | "odoo" | "share" | "manual" | null;
  ref: string | null;
  file_id: string | null;
  archivo: string | null;
  fila_excel: number | null;
  producto_chn: string | null;
  /** Filas que comparten cartón: sobre ellas se reparte el flete, por pieza. */
  grupo: number[];

  // números
  precio_usd: number | null;
  piezas_grupo: number | null;
  /** Los crudos del renglon: piezas TOTALES y cuantos cartones. */
  /** El unitario USD lo capturo una persona, no el archivo. */
  precio_manual?: boolean;
  piezas_fila: number | null;
  cajas: number | null;
  cbm_pieza: number | null;
  peso_total: number | null;
  peso_pieza: number | null;
  flete: number | null;
  producto_mxn: number | null;
  costo: number | null;
  /** De dónde salió el costo de producto: del packing list o del ya guardado. */
  origen_prod: "packing_list" | "kubera" | null;
  caja_lwh: [number, number, number] | null;
  pieza_lwh: [number, number, number] | null;

  // contraste con lo que hay hoy en costos_validados
  costo_viejo: number | null;
  peso_viejo: number | null;
  revisado_at: string | null;

  // fotos (data URI) y candidatos para decidir a mano
  img_odoo: string | null;
  img_ml: string | null;
  img_pl: string | null;
  cands_ia: PublicadoCandidatoIA[];
  cands_img: Record<string, string>;
  cands_txt: Record<string, string>;
  veredicto: PublicadoVeredicto[];
}

export interface PublicadosResumen {
  total: number;
  resueltos: number;
  sha256: number;
  dhash: number;
  ia: number;
  sin_match: number;
  sin_insumo: number;
  ya_validados: number;
}

export interface PublicadosGuardado {
  escritos: number;
  detalle: { sku: string; costo: number | null; costo_anterior: number | null }[];
  saltados: { sku: string; motivo: string; detalle?: string }[];
  errores: { sku: string; error: string }[];
}

export interface PublicadosOpciones {
  tarifa_mxn_m3: number;
  tipo_cambio: number;
  usar_ia: boolean;
}

export interface PublicadosEstado {
  id: string;
  /** encolado|validando|expandiendo|ruteando|bajando|indexando|escalera|calculando|listo|error */
  paso: string;
  paso_label: string;
  actual: number;
  total: number;
  creado_en?: string | null;
  expira_en?: string | null;
  opciones?: PublicadosOpciones;
  filas: FilaPublicado[];
  omitidos: PublicadoOmitido[];
  avisos: string[];
  resumen?: PublicadosResumen;
  guardado?: PublicadosGuardado | null;
  error?: string | null;
}

/** Lo que devuelve el POST que arranca el trabajo. */
export interface PublicadosArranque {
  id: string;
  paso: string;
  paso_label: string;
  total?: number;
  omitidos?: PublicadoOmitido[];
}

// ── Publicaciones por tienda (pestaña Omnicanal) ─────────────────────────────
//
// Contrato CERRADO del backend (`GET /api/publicaciones`, v0.251.0). Los tres
// vocabularios de abajo son cerrados: si el canal manda un valor nuevo, el
// backend lo mete en `desconocido`/`desconocida` con el crudo al lado; NUNCA
// lo aplasta a "activa" ni a "sin oferta". El front tampoco debe hacerlo.

/** Estado NORMALIZADO. Cada canal usa su propio crudo; esto es lo que se pinta. */
export type EstadoNormalizado =
  | "activa"              // se puede comprar AHORA
  | "puede_estar_activa"  // el canal no distingue activo de inactivo (Temu)
  | "no_comprable"        // existe y se ve, pero no se vende (Amazon DISCOVERABLE)
  | "pausada"
  | "en_revision"
  | "borrador"
  | "rechazada"
  | "cerrada"
  | "sin_estado"          // el canal no reporta — NO es "no hay"
  | "desconocido";        // valor nuevo que nadie mapeó

/**
 * TRES estados, no dos. `desconocida` = nadie le ha preguntado al canal por el
 * precio de campaña; decir "sin oferta" ahí sería inventar una observación.
 */
export type OfertaEstado = "con_oferta" | "sin_oferta" | "desconocida";

/** Por qué NO se puede saber el margen. Llega junto a `margen_pct: null`. */
export type MargenMotivo =
  | "sin_costo_del_canal"
  | "sin_comision"
  | "sin_peso"
  | "sin_precio";

export interface Publicacion {
  sku: string;
  titulo: string | null;
  canal: string;
  /** Firma de "ya revisé este costeo" (mig. 0032). Con ella, la alerta de
      costo dudoso se calla: el 1.5× adivina, esto es un hecho. */
  revisado_at?: string | null;
  /** `legacy_code` de la cuenta: BEKURA, SANCORFASHION, AMAZON, KUBERA… */
  tienda: string | null;
  listing_id: string | null;
  url: string | null;
  estado: EstadoNormalizado;
  /** El valor tal cual lo manda el canal. Se muestra cuando `estado` es `desconocido`. */
  estado_crudo: string | null;
  precio_lista: number | null;
  /** Contra ESTO se calcula el margen: la oferta si la hay, si no el de lista. */
  precio_vigente: number | null;
  moneda: string;
  oferta_estado: OfertaEstado;
  oferta_precio: number | null;
  oferta_desc_pct: number | null;
  oferta_vista_at: string | null;
  /** Antigüedad de la observación, en días. OBLIGATORIO junto a la oferta. */
  oferta_dias: number | null;
  /** `null` = NO SE PUEDE SABER (ver `margen_motivo`). Jamás tratarlo como 0. */
  margen_pct: number | null;
  roi: number | null;
  ganancia_neta: number | null;
  margen_motivo: MargenMotivo | null;
  costo_unitario: number | null;
  costo_comision: number | null;
  costo_fee_envio: number | null;
  iva_mnt: number | null;
  pct_comision: number | null;
  comision_estimada: number | null;
  stock_own: number | null;
  stock_full: number | null;
  stock_fba: number | null;
  es_full: boolean | null;
  visto_at: string | null;
}

/** El censo de UN canal. Aparece SIEMPRE, aunque venga en ceros. */
export interface CoberturaCanal {
  canal: string;
  publicaciones: number;
  activas: number;
  con_margen: number;
  sin_margen: number;
  /** `null` = no aplica (0 publicaciones). NO es 0%. */
  pct_con_margen: number | null;
  motivos: Record<string, number>;
  con_oferta: number;
  sin_oferta: number;
  oferta_desconocida: number;
  oferta_mas_vieja_dias: number | null;
  /** Por qué este canal cuenta lo que cuenta. Se pinta junto al número. */
  nota: string | null;
}

export interface CoberturaPublicaciones {
  publicaciones: number;
  con_margen: number;
  sin_margen: number;
  pct_con_margen: number | null;
  canales: CoberturaCanal[];
  /** Texto listo del backend: este margen es PROSPECTIVO, no realizado. */
  aviso: string;
}

/**
 * Por qué el refresco quedó como quedó. Vocabulario CERRADO del backend
 * (`services/precio_al_abrir.py`). Es para EXPLICAR, no para decidir: lo que
 * se mira es `al_dia`.
 */
export type RefrescoEstado =
  | "ok"                 // se le preguntó a ML y contestó
  | "piso"               // observado hace menos de 5 min: no hacía falta preguntar
  | "sin_publicaciones"  // el SKU no tiene publicaciones vivas de ML
  | "apagado"            // PRECIO_AL_ABRIR o SYNC_ENABLED en false
  | "no_aplica"          // no se mandó `q`
  | "sin_token"          // la cuenta no tiene token
  | "fallo"
  | "timeout";

/**
 * `refresco` — confirmación del precio de ML CONTRA ML, hecha antes de
 * contestar. Sólo viene cuando se pidió `refrescar=true`, y hoy eso ocurre en
 * UN solo lugar: al abrir el cajón de un producto (`ProductDetailDrawer`).
 * Nunca desde una rejilla — una apertura son 1 o 2 llamadas a ML, cien filas
 * serían doscientas.
 *
 * `al_dia` es EL ÚNICO campo obligatorio del bloque y lo único que hay que
 * mirar. **No lo recalcules** a partir de `estado`: el backend lo arma con
 * `estado ∈ {ok, piso, sin_publicaciones}` Y sin fallos ni omisiones por tope,
 * así que cuando entre un caso nuevo el booleano lo absorbe y una condición
 * escrita a mano aquí se quedaría vieja sin avisar.
 *
 * `al_dia: false` NO es un error de carga: la respuesta trae exactamente los
 * mismos precios que traería sin refrescar. Se pinta lo guardado DICIENDO que
 * es lo guardado.
 */
export interface RefrescoPrecio {
  /** "Todo lo que se muestra de ML para este SKU está confirmado ahora mismo." */
  al_dia: boolean;
  estado?: RefrescoEstado;
  /** Publicaciones de ML vivas de ese SKU. */
  publicaciones?: number;
  /** A cuántas se les preguntó a ML. */
  preguntadas?: number;
  /** Cuántas quedaron selladas. */
  confirmadas?: number;
  /** De ésas, cuántas traían otro número. */
  cambiaron?: number;
  /** ML no contestó, o falta el token de esa cuenta. */
  sin_respuesta?: number;
  /** El piso de 5 min las salvó: no hacía falta preguntar. */
  omitidas_piso?: number;
  /** Tope por producto (hoy nunca: máximo 2 por SKU). */
  omitidas_tope?: number;
  ms?: number;
  detalle?: string | null;
}

export interface PublicacionesResp {
  total: number;
  page: number;
  per_page: number;
  items: Publicacion[];
  cobertura: CoberturaPublicaciones;
  /** Sólo con `refrescar=true`. Ver `RefrescoPrecio`. */
  refresco?: RefrescoPrecio;
}
