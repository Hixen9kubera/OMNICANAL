/**
 * Contratos de datos del laboratorio — espejo de `backend/sandbox_precios/DISENO.md`
 * secciones 6 y 7. Si cambia allá, cambia aquí.
 *
 * Reglas de lectura que valen para todo el archivo:
 *   · Montos en MXN CON IVA, salvo `costo` (SIN IVA: el contenedor de 525,000 es
 *     sin IVA según `lib/margen.ts:100` del panel).
 *   · `null` = sin dato. Nunca se pinta como 0: un cero inventado fue lo que
 *     escondió 754 publicaciones de ML en el panel (memoria «cruces siempre en vivo»).
 *   · Fechas ISO.
 *
 * Los campos marcados «extensión» NO están en DISENO.md: la web los usa si llegan
 * y funciona sin ellos. Se listan en el reporte para que api.py los considere.
 */

export type Canal = "mercado_libre" | "amazon" | "walmart" | "temu" | "tiktok";
export type EstadoPub = "activa" | "pausada" | "en_revision" | "cerrada" | "inactiva" | "otra";
export type FuenteCosto = "packing_list_exacto" | "prorrateo_kubera" | "tarifa_7500" | "sin_costo";
export type Confianza = "alta" | "media" | "baja";

// ── estado.json ─────────────────────────────────────────────────────────────
export interface Etapa {
  ok: boolean;
  filas?: number | null;
  duracion_s?: number | null;
  avisos?: string[];
}
export interface Estado {
  generado_at: string | null;
  version?: string;
  etapas?: Record<string, Etapa>;
  frescura?: Partial<Record<
    "ml_listings" | "amazon_listings" | "walmart_listings" | "competencia_serp"
    | "competencia_best" | "ventas" | "visitas_api", string | null>> & Record<string, string | null>;
  contadores_ml_api?: Record<string, number>;
  snapshots?: string[];
  /** extensión: api.py sin LAB_ACCESS_KEY fuera de Railway (DISENO §7 «modo local, banner»). */
  modo_local?: boolean;
  /** extensión: una corrida del pipeline en curso. */
  corriendo?: boolean;
  /** extensión de api.py: modo de acceso, aviso y bitácora del pipeline. */
  servidor?: {
    version?: string;
    modo_auth?: "llave" | "abierto" | "cerrado" | string;
    aviso?: string | null;
    hora_pipeline_utc?: string;
    archivos?: Record<string, { actualizado_at: string; bytes: number } | null>;
  };
}

// ── publicaciones.json ──────────────────────────────────────────────────────
export interface CostoPub {
  unitario: number | null;
  fuente: FuenteCosto;
  contenedor?: string | null;
  validado?: boolean;
  revisado_por?: string | null;
  costo_panel?: number | null;
}
export interface CompetidorLista {
  titulo?: string | null;
  precio: number;
  vendedor?: string | null;
  url?: string | null;
  vendidos?: number | null;
}
export interface Competencia {
  n: number;
  promedio: number | null;
  mediana: number | null;
  minimo: number | null;
  maximo: number | null;
  fuente: "serp" | "bestsellers" | "sugerido_ml" | string;
  capturado_en: string | null;
  sugerido_ml?: number | null;
  /** extensión: los precios que forman la mediana (el cajón los lista si vienen). */
  lista?: CompetidorLista[];
}
export interface Publicacion {
  id: string;
  canal: Canal;
  cuenta: string;
  listing_id: string;
  sku: string | null;
  titulo: string | null;
  url?: string | null;
  thumbnail?: string | null;
  categoria_id?: string | null;
  estado: EstadoPub;
  situacion?: string | null;
  sub_status?: string[] | null;
  logistica?: string | null;
  es_full: boolean;
  precio_cobrado: number | null;
  precio_lista: number | null;
  promo?: { tipo: string; fin: string | null } | null;
  stock_full: number | null;
  stock_propio: number | null;
  stock_odoo: number | null;
  costo: CostoPub | null;
  comision_pct: number | null;
  comision: number | null;
  envio: number | null;
  iva: number | null;
  utilidad: number | null;
  margen_pct: number | null;
  visitas_30d: number | null;
  unidades_30d: number | null;
  conversion_30d: number | null;
  ingreso_30d: number | null;
  competencia: Competencia | null;
  tags_calidad?: string[];
  frescura_at?: string | null;
  avisos?: string[];
  supuesto_canal: boolean;
}

// ── precios.json ────────────────────────────────────────────────────────────
export interface ParActual { actual: number | null; recomendado: number | null }
export interface Elasticidad {
  beta: number | null;
  beta_visitas: number | null;
  beta_conversion: number | null;
  fuente: "item" | "categoria" | "global" | "prior";
  n_semanas: number | null;
  confianza: Confianza;
}
export interface PasoPlan { fecha: string; precio: number; nota?: string }
export interface Recomendacion {
  id: string;
  sku: string | null;
  cuenta: string;
  listing_id: string;
  titulo: string | null;
  estado: EstadoPub;
  precio_actual: number | null;
  precio_recomendado: number | null;
  cambio_pct: number | null;
  precio_equilibrio: number | null;
  precio_piso: number | null;
  precio_max_utilidad: number | null;
  precio_max_volumen: number | null;
  precio_ref_competencia: number | null;
  fuente_ref: string | null;
  unidades_dia: ParActual;
  visitas_dia: ParActual;
  conversion: ParActual;
  utilidad_dia: ParActual;
  margen: ParActual;
  elasticidad: Elasticidad;
  stock: { full: number | null; odoo: number | null; cobertura_dias: number | null };
  razones: string[];
  plan: { modo: "semanal" | "diario" | string; pasos: PasoPlan[] } | null;
  autorizacion: "pendiente" | string;
}

// ── curvas.json ─────────────────────────────────────────────────────────────
export interface PuntoCurva {
  precio: number;
  visitas_dia: number | null;
  conversion: number | null;
  unidades_dia: number | null;
  utilidad_unit: number | null;
  utilidad_dia: number | null;
  margen_pct: number | null;
}
export interface Curva {
  puntos: PuntoCurva[];
  marcadores: Partial<Record<"actual" | "recomendado" | "piso" | "equilibrio" | "max_utilidad" | "ref_competencia", number | null>>;
}

// ── historial.json ──────────────────────────────────────────────────────────
export interface PuntoHistorial {
  fecha: string;
  precio_realizado: number | null;
  unidades: number | null;
  visitas: number | null;
  precio_ofrecido: number | null;
  precio_recomendado: number | null;
}
export interface Historial { serie: PuntoHistorial[] }

// ── packing100.json / contenedores.json ─────────────────────────────────────
export type MetodoCosto = "volumetrico_real" | "tarifa_fija_7500" | "peso_volumen_wm" | "valor_fob" | "hibrido_70_30";
export interface FilaPacking {
  sku: string;
  titulo: string | null;
  cuentas: string[];
  listing_ids: string[];
  precio_cobrado: number | null;
  unidades_30d: number | null;
  odoo: { container_numbers: string | null; codigos: string[] };
  archivo: { nombre: string; file_id?: string | null; sha256?: string | null; contenedor: string | null } | null;
  fila: number | null;
  empate: { metodo: "ferraforme" | "sha256" | "dhash" | "ia_titulo" | "ia_foto" | string; distancia: number | null; confianza: Confianza };
  cajas: number | null;
  piezas_fila: number | null;
  piezas_grupo: number | null;
  caja_mixta: boolean;
  cbm_caja: number | null;
  cbm_pieza: number | null;
  peso_pieza_kg: number | null;
  precio_usd: number | null;
  costos: Record<MetodoCosto, number | null>;
  kubera: {
    costo_total: number | null; costo_cbm: number | null; costo_producto: number | null;
    revisado_at: string | null; revisado_por: string | null; validado: boolean;
  } | null;
  margen_con_525k: number | null;
  margen_panel: number | null;
}
export interface Packing {
  generado_at: string | null;
  contenedor_mxn: number;
  filas: FilaPacking[];
  /** api.py agrega `packing_saltados.json`: los SKUs que no se pudieron ubicar y por qué. */
  saltados?: { sku: string; motivo: string }[];
}
export interface Contenedor {
  codigo: string;
  archivo: string | null;
  sha256?: string | null;
  total_cbm: number | null;
  total_piezas: number | null;
  total_peso_kg: number | null;
  total_usd: number | null;
  costo_m3: number | null;
  rango_ok: boolean;
  renglones: number | null;
  renglones_sin_cbm: number | null;
  cajas_mixtas: number | null;
  skus_en_lote: number | null;
}

// ── metricas.json ───────────────────────────────────────────────────────────
export interface MetricasCuenta {
  publicaciones: number; activas: number; activas_full: number; pausadas_full: number;
  pausadas_full_sin_stock: number; pausadas_full_con_stock_odoo: number;
  visitas_dia: number | null; unidades_dia: number | null; conversion: number | null;
  ingreso_30d: number | null; margen_mediano: number | null; perdiendo_dinero: number;
  con_costo: number; con_competencia: number;
}
export interface Palanca {
  tipo: "reactivar_full" | string;
  sku: string | null;
  cuenta: string;
  listing_id: string;
  ventas_perdidas_dia: number | null;
  stock_odoo: number | null;
  motivo: string | null;
  /** extensión: para enlazar al historial. */
  id?: string;
  titulo?: string | null;
}
export interface Metricas {
  generado_at?: string | null;
  por_cuenta: Record<string, MetricasCuenta>;
  por_canal: Record<string, { publicaciones: number; activas: number; frescura: string | null }>;
  embudo: { visitas_30d: number | null; unidades_30d: number | null };
  elasticidad: {
    global: number | null;
    por_categoria: { categoria: string; beta: number | null; n: number; nombre?: string | null }[];
    /** El contrato no fija la forma: se aceptan contenedores {desde,hasta,n} o una lista de betas. */
    histograma: ({ desde: number; hasta: number; n: number } | number)[];
  };
  palancas: Palanca[];
  serie_diaria: { fecha: string; cuenta: string; unidades: number | null; ingreso: number | null; visitas: number | null }[];
}

// ── Sobres de la API (DISENO §7 no los fija; la web acepta variantes) ────────
export interface Pagina<T> {
  generado_at?: string | null;
  total: number;
  page: number;
  per_page: number;
  filas: T[];
  /** extensión: conteos por canal:cuenta para las píldoras. */
  conteos?: Record<string, number>;
  parametros?: Record<string, unknown>;
}
