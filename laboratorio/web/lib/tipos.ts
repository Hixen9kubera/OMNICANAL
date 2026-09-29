/**
 * Contratos de datos del laboratorio — espejo de `backend/sandbox_precios/DISENO.md`
 * secciones 6 y 7, AJUSTADO al JSON real del 28-sep (8,840 publicaciones, 1,744
 * filas de precios, 100 SKUs de packing, 40 contenedores). Si cambia allá, cambia aquí.
 *
 * Reglas de lectura que valen para todo el archivo:
 *   · Montos en MXN CON IVA, salvo `costo` (SIN IVA: el contenedor de 525,000 es
 *     sin IVA según `lib/margen.ts:100` del panel).
 *   · `null` = sin dato. Nunca se pinta como 0: un cero inventado fue lo que
 *     escondió 754 publicaciones de ML en el panel (memoria «cruces siempre en vivo»).
 *   · Fechas ISO.
 *
 * Casi todo campo que no está en DISENO §6 es opcional: la web lo usa si llega y
 * funciona sin él.
 */

export type Canal = "mercado_libre" | "amazon" | "walmart" | "temu" | "tiktok";
export type EstadoPub = "activa" | "pausada" | "en_revision" | "cerrada" | "inactiva" | "otra";
/** `sospechoso`: el m³ es imposible para su peso facturable; el costo se descarta (unitario null). */
export type FuenteCosto = "packing_list_exacto" | "prorrateo_kubera" | "tarifa_7500" | "sin_costo" | "sospechoso";
export type Confianza = "alta" | "media" | "baja";

// ── estado.json (+ lo que agrega api.py) ────────────────────────────────────
export interface Etapa {
  ok: boolean;
  saltada?: boolean;
  filas?: number | null;
  duracion_s?: number | null;
  avisos?: string[];
  error?: string | null;
}
export interface CorridaPipeline {
  origen?: string | null;
  inicio?: string | null;
  fin?: string | null;
  duracion_s?: number | null;
  ok?: boolean;
  error?: string | null;
  sin_ml?: boolean;
}
export interface Estado {
  generado_at: string | null;
  version?: string;
  etapas?: Record<string, Etapa>;
  /** Casi todo ISO; `canales_max_updated_at` es un objeto (por eso `unknown`). */
  frescura?: Record<string, unknown>;
  contadores_ml_api?: Record<string, number>;
  snapshots?: string[];
  /**
   * Regla de Brandon (28-sep): el costo es el prorrateo de los 525,000 por
   * contenedor; la mercancía del packing list NO se incluye (true). Con false las
   * bajadas que quedarían bajo el piso con el costo del panel se bloquean.
   */
  incluye_mercancia?: boolean | null;
  /** Los números de negocio con que se calculó (contenedor_mxn, piso_margen, topes…). */
  parametros_clave?: Record<string, unknown> | null;
  /** api.py sin LAB_ACCESS_KEY fuera de Railway (DISENO §7 «modo local, banner»). */
  modo_local?: boolean;
  /** Una corrida del pipeline en curso. */
  corriendo?: boolean;
  servidor?: {
    version?: string;
    modo_auth?: "llave" | "abierto" | "cerrado" | string;
    aviso?: string | null;
    hora_pipeline_utc?: string;
    auto_pipeline?: boolean;
    /** ¿La web puede ofrecer «Recalcular datos»? (api.py `_recalcular_permitido`). */
    recalcular?: { permitido: boolean; motivo?: string | null };
    pipeline?: {
      corriendo?: boolean;
      inicio?: string | null;
      origen?: string | null;
      sin_ml?: boolean;
      proxima?: string | null;
      ultima?: CorridaPipeline | null;
    };
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
  marca?: string | null;
  /** El aviso de costo más importante, en texto (costo_imposible > costo_menor_a_1_peso > riesgo > costo_bajo_fob). */
  aviso?: string | null;
  avisos?: string[];
  /** Costo que se descartó por `costo_imposible` (fuente `sospechoso`). */
  unitario_descartado?: number | null;
  mercancia_mxn?: number | null;
  costo_bajo_fob?: boolean;
  /** Margen con el costo del PANEL (mercancía + flete): solo informativo mientras incluye_mercancia = true. */
  riesgo_mercancia?: { costo_panel: number | null; piso: number | null; al_precio_cobrado: number | null; precio_recomendado: number | null; al_recomendado: number | null } | null;
}
/** Un competidor de la muestra SERP. `id` es de ML: MLM… (producto) o MLMU… (user product). */
export interface CompetidorLista {
  titulo?: string | null;
  precio: number;
  vendedor?: string | null;
  visitas_30d?: number | null;
  id?: string | null;
  url?: string | null;
  vendidos?: number | null;
}
export interface Competencia {
  /** `null` cuando la referencia no es una muestra (sugerido_ml, price_to_win). */
  n: number | null;
  promedio: number | null;
  mediana: number | null;
  minimo: number | null;
  maximo: number | null;
  fuente: "serp" | "bestsellers" | "sugerido_ml" | "price_to_win" | string;
  capturado_en: string | null;
  sugerido_ml?: number | null;
  mediana_filtrada?: number | null;
  n_filtrado?: number | null;
  prom_pond_visitas?: number | null;
  termino?: string | null;
  edad_dias?: number | null;
  nota?: string | null;
  categoria_id?: string | null;
  lista?: CompetidorLista[];
  bestsellers?: { n: number; mediana: number | null; minimo: number | null; maximo: number | null; categoria_id?: string | null; capturado_en?: string | null } | null;
  price_to_win?: { precio: number | null; status?: string | null; precio_ganador?: number | null; catalog_product_id?: string | null } | null;
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
  /** Qué es «otra» (no_comprable, borrador, rechazada…) o el matiz de una pausada (incompleta). */
  estado_detalle?: string | null;
  situacion?: string | null;
  sub_status?: string[] | null;
  logistica?: string | null;
  es_full: boolean;
  precio_cobrado: number | null;
  fuente_precio?: string | null;
  precio_lista: number | null;
  promo?: { tipo: string; fin: string | null; inicio?: string | null; monto?: number | null; regular?: number | null } | null;
  stock_full: number | null;
  stock_propio: number | null;
  stock_odoo: number | null;
  costo: CostoPub | null;
  peso?: { kg: number | null; fuente: string | null } | null;
  comision_pct: number | null;
  fuente_comision?: string | null;
  comision: number | null;
  envio: number | null;
  iva: number | null;
  utilidad: number | null;
  margen_pct: number | null;
  visitas_30d: number | null;
  visitas_fuente?: string | null;
  unidades_30d: number | null;
  conversion_30d: number | null;
  ingreso_30d: number | null;
  unidades_150d?: number | null;
  vendidas_total?: number | null;
  competencia: Competencia | null;
  tags_calidad?: string[];
  catalogo?: boolean | null;
  frescura_at?: string | null;
  avisos?: string[];
  supuesto_canal: boolean;
  /** Sólo ML FULL (vienen de precios.json); `null` en el resto. */
  precio_recomendado?: number | null;
  cambio_pct?: number | null;
  /** Contra qué se mide `cambio_pct`: en pausadas, el precio realizado de su base. */
  cambio_ref?: CambioRef | null;
  cambio_vs_actual?: number | null;
  razones?: string[] | null;
  autorizacion?: string | null;
  /** Otros canales: precio que iguala el margen objetivo (el recomendado de ML o el piso del canal). */
  precio_paridad?: number | null;
  paridad?: { margen_objetivo: number | null; fuente: string | null; margen_ml?: number | null; precio_ml_recomendado?: number | null; diferencia_pct?: number | null } | null;
}

// ── precios.json ────────────────────────────────────────────────────────────
export interface ParActual { actual: number | null; recomendado: number | null }
export interface Elasticidad {
  beta: number | null;
  beta_visitas: number | null;
  beta_conversion: number | null;
  fuente: "item" | "categoria" | "global" | "prior" | string;
  n_semanas: number | null;
  confianza: Confianza;
}
export type CambioRef = "precio_actual" | "precio_realizado_base";
export interface PasoPlan {
  fecha: string; precio: number;
  /** prueba · al_reactivar · renovar_promo */
  tipo?: string | null; nota?: string | null;
  /** Paso mínimo de $1 que pasa el tope (precios bajos). */
  excede_tope?: boolean;
  /** renovar_promo: el paso es una promoción a `precio_promo` sobre `precio_regular`. */
  precio_promo?: number | null; precio_regular?: number | null;
}
export interface Plan {
  modo: "semanal" | "diario" | "al_reactivar" | "renovar_promo" | string;
  /** renovar_promo: la promoción vigente vence este día y el plan empieza ahí. */
  promo_fin?: string | null;
  precio_regular?: number | null;
  pasos_exceden_tope?: Record<string, number> | null;
  pasos: PasoPlan[];
  nota?: string | null;
  prueba?: boolean;
  /** El mismo plan con topes diarios (sólo en `semanal`). */
  diario?: { modo: string; pasos: PasoPlan[] } | null;
}
/** `fuera`: qué precios EXACTOS quedan fuera de la gráfica (la rejilla va de 0.55·P0 a 1.45·P0). */
export interface Rejilla { min: number; max: number; fuera: string[] }
export interface Recomendacion {
  id: string;
  sku: string | null;
  cuenta: string;
  listing_id: string;
  titulo: string | null;
  thumbnail?: string | null;
  url?: string | null;
  estado: EstadoPub;
  categoria_id?: string | null;
  precio_actual: number | null;
  fuente_precio_actual?: string | null;
  precio_recomendado: number | null;
  /** Contra `cambio_ref`: activas → precio de hoy; pausadas → precio realizado de su base. */
  cambio_pct: number | null;
  cambio_ref?: CambioRef | null;
  cambio_vs_actual?: number | null;
  cambio_vs_realizado?: number | null;
  precio_regular?: number | null;
  promo_dias_para_fin?: number | null;
  /** Lo que proponía el modelo antes de los guardarraíles (si lo cambiaron). */
  recomendado_modelo?: number | null;
  recomendado_sin_guardarrailes?: number | null;
  precio_equilibrio: number | null;
  precio_piso: number | null;
  precio_max_utilidad: number | null;
  precio_max_volumen: number | null;
  precio_ref_competencia: number | null;
  fuente_ref: string | null;
  ref_confiable?: boolean;
  sugerido_ml?: number | null;
  mediana_bestsellers?: number | null;
  unidades_dia: ParActual;
  visitas_dia: ParActual;
  conversion: ParActual;
  utilidad_dia: ParActual;
  margen: ParActual;
  elasticidad: Elasticidad;
  stock: { full: number | null; odoo: number | null; cobertura_dias: number | null; cobertura_dias_recomendado?: number | null };
  costo?: { unitario: number | null; fuente: FuenteCosto; costo_panel?: number | null; margen_costo_panel_recomendado?: number | null } | null;
  peso?: { kg: number | null; fuente: string | null } | null;
  /** Ventana de los 28 días NO censurados que definen U0/V0/P0. */
  base?: { dias: number | null; desde: string | null; hasta: string | null; unidades: number | null; visitas: number | null; p_base: number | null; factor_estacional_info?: number | null } | null;
  promo?: Publicacion["promo"];
  sacrificio?: number | null;
  rejilla?: Rejilla | null;
  razones: string[];
  avisos?: string[];
  plan: Plan | null;
  autorizacion: "pendiente" | string;
  utilidad_unit_actual?: number | null;
  utilidad_unit_recomendado?: number | null;
  comision_pct_actual?: number | null;
  fuente_comision?: string | null;
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
  rejilla?: Rejilla | null;
}

// ── historial.json ──────────────────────────────────────────────────────────
export interface PuntoHistorial {
  fecha: string;
  precio_realizado: number | null;
  unidades: number | null;
  visitas: number | null;
  precio_ofrecido: number | null;
  precio_recomendado: number | null;
  /** Punto de un snapshot del laboratorio: el precio que se cobraba ese día. */
  precio_cobrado?: number | null;
  snapshot?: boolean;
  /** Ese día la publicación no tenía oferta (pausada / sin stock). */
  sin_oferta?: boolean;
}
export interface Historial { serie: PuntoHistorial[] }
/** GET /historial?ids=a,b,c&dias=90 (lote para las mini-gráficas). */
export interface HistorialLote {
  dias: number;
  desde: string;
  hasta: string;
  total: number;
  series: Record<string, { serie: Partial<PuntoHistorial>[] }>;
  sin_historial: string[];
}

// ── packing100.json / contenedores.json ─────────────────────────────────────
export type MetodoCosto = "volumetrico_real" | "tarifa_fija_7500" | "peso_volumen_wm" | "valor_fob" | "hibrido_70_30";
export interface EconomiaPacking {
  precio: number | null; comision_pct: number | null; comision_fuente?: string | null; comision: number | null;
  envio: number | null; iva: number | null; costo: number | null; utilidad: number | null; margen: number | null; faltan?: string[];
}
export interface FilaPacking {
  sku: string;
  padre?: string | null;
  titulo: string | null;
  cuentas: string[];
  listing_ids: string[];
  listing_principal?: string | null;
  cuenta_principal?: string | null;
  es_full?: boolean;
  precio_cobrado: number | null;
  unidades_30d: number | null;
  odoo: { container_numbers: string | null; codigos: string[] };
  archivo: { nombre: string; file_id?: string | null; sha256?: string | null; contenedor: string | null } | null;
  fila: number | null;
  texto_fila?: string | null;
  empate: { metodo: "ferraforme" | "sha256" | "dhash" | "ia_titulo" | "ia_foto" | string; distancia: number | null; confianza: Confianza; detalle?: string | null; segunda_opinion?: string | null };
  cajas: number | null;
  piezas_fila: number | null;
  piezas_grupo: number | null;
  caja_mixta: boolean;
  cbm_caja: number | null;
  cbm_pieza: number | null;
  peso_pieza_kg: number | null;
  precio_usd: number | null;
  costos: Record<MetodoCosto, number | null>;
  /** W/M «de contenedor completo»: kg/m³ = carga útil ÷ 70 m³ (en vez de 1,000). */
  costo_wm_fcl?: number | null;
  contenedor_costo_m3?: number | null;
  kubera: {
    costo_total: number | null; costo_cbm: number | null; costo_producto: number | null;
    revisado_at: string | null; revisado_por: string | null; validado: boolean;
  } | null;
  comision_real_30d?: number | null;
  comision_real_cobertura?: number | null;
  economia_525k?: EconomiaPacking | null;
  economia_panel?: EconomiaPacking | null;
  margen_con_525k: number | null;
  margen_panel: number | null;
  margenes_por_metodo?: Partial<Record<MetodoCosto, number | null>>;
  avisos?: string[];
}
export interface Packing {
  generado_at: string | null;
  contenedor_mxn: number;
  metodo_recomendado?: string | null;
  resumen?: {
    margen_mediano_525k?: number | null;
    margen_mediano_panel?: number | null;
    perdiendo_con_525k?: number | null;
    perdiendo_con_panel?: number | null;
    con_costo_validado?: number | null;
    [k: string]: unknown;
  } | null;
  filas: FilaPacking[];
  /** api.py agrega `packing_saltados.json`: los SKUs que no se pudieron ubicar y por qué. */
  saltados?: { sku: string; motivo: string; detalle?: string | null; unidades_30d?: number | null }[];
}
export interface Contenedor {
  /** OJO: el código se repite (dos packing lists del mismo contenedor): la llave única es `file_id`. */
  codigo: string;
  codigos?: string[];
  file_id?: string | null;
  archivo: string | null;
  sha256?: string | null;
  total_cbm: number | null;
  total_piezas: number | null;
  total_peso_kg: number | null;
  total_usd: number | null;
  /** 525,000 ÷ m³ totales del archivo. */
  costo_m3: number | null;
  costo_m3_wm?: number | null;
  /** 525,000 ÷ Σ max(m³, kg ÷ (carga útil/70)): el W/M de contenedor completo. */
  costo_m3_wm_fcl?: number | null;
  diferencia_vs_7500?: number | null;
  rango_ok: boolean;
  renglones: number | null;
  renglones_sin_cbm: number | null;
  cajas_mixtas: number | null;
  /** SKUs de la muestra que salieron de este contenedor (lista; antes el contrato decía número). */
  skus_en_lote: string[] | number | null;
  /** El peso total supera el 90 % de la carga útil: el contenedor se llenó por kilos. */
  limitado_por_peso?: boolean;
  peso_vs_carga_util?: number | null;
  /** Fracción del m³ cuyos renglones traen peso: con poca cobertura, la carga útil se subestima. */
  cobertura_peso?: number | null;
  densidad_kg_m3?: number | null;
  avisos?: string[];
}

// ── metricas.json ───────────────────────────────────────────────────────────
export interface MetricasCuenta {
  publicaciones: number; activas: number; activas_full: number; pausadas_full: number;
  pausadas_full_sin_stock: number; pausadas_full_con_stock_odoo: number;
  visitas_dia: number | null; unidades_dia: number | null; conversion: number | null;
  ingreso_30d: number | null; margen_mediano: number | null; perdiendo_dinero: number;
  con_costo: number; con_competencia: number;
  visitas_30d?: number | null; unidades_30d?: number | null;
}
export interface MetricasCanal {
  publicaciones: number;
  activas: number;
  frescura: string | null;
  por_estado?: Record<string, number>;
  por_detalle?: Record<string, number>;
  con_margen?: number | null;
  margen_mediano_activas?: number | null;
  unidades_30d?: number | null;
  ingreso_30d?: number | null;
  supuesto_canal?: boolean;
  aviso_frescura?: string | null;
}
interface PalancaBase {
  id?: string;
  sku: string | null;
  cuenta: string;
  listing_id: string;
  titulo?: string | null;
  stock_odoo: number | null;
  precio_cobrado?: number | null;
  precio_recomendado?: number | null;
  motivo: string | null;
}
/** Pausada sin stock en FULL con piezas libres en Odoo: la venta que se pierde por no enviar. */
export interface PalancaReactivar extends PalancaBase {
  tipo: "reactivar_full";
  ventas_perdidas_dia: number | null;
  fuente_velocidad?: string | null;
  ingreso_perdido_dia?: number | null;
  utilidad_perdida_dia?: number | null;
  dias_de_stock_odoo?: number | null;
  piezas_recuperables_30d?: number | null;
  precio_base?: number | null;
  utilidad_unit_base?: number | null;
  cuentas_con_el_sku?: string[];
}
/** Activa que pierde dinero por unidad al precio de hoy. */
export interface PalancaPerdida extends PalancaBase {
  tipo: "perdiendo_dinero";
  ventas_perdidas_dia: null;
  unidades_dia?: number | null;
  utilidad_unit?: number | null;
  perdida_dia?: number | null;
  precio_equilibrio?: number | null;
  precio_piso?: number | null;
  costo_fuente?: string | null;
}
export type Palanca = PalancaReactivar | PalancaPerdida;
export interface ValidacionResumen {
  pliegue?: string;
  semanas_prueba?: string[] | null;
  semanas_entreno?: string[] | null;
  tope_tau2?: number | string | null;
  criterio?: string | null;
  item_semanas_con_cambio?: number | null;
  wape: { modelo: number | null; ingenuo: number | null; cero: number | null };
  sesgo: { modelo: number | null; ingenuo: number | null };
  devianza: { modelo: number | null; ingenuo: number | null };
  tope_por_devianza?: number | string | null;
  modelo_vs_ingenuo?: number | null;
}
export interface Metricas {
  generado_at?: string | null;
  por_cuenta: Record<string, MetricasCuenta>;
  /** Incluye `mercado_libre` (las dos cuentas juntas). */
  por_canal: Record<string, MetricasCanal>;
  embudo: { visitas_30d: number | null; unidades_30d: number | null; ingreso_30d?: number | null; ventana?: string[]; nota?: string | null };
  elasticidad: {
    global: number | null;
    global_visitas?: number | null;
    por_categoria: { categoria: string; beta: number | null; n: number; nombre?: string | null; beta_visitas?: number | null }[];
    /** Contenedores {desde, hasta, n[, crudas]} (o, en fixtures viejas, una lista de betas). */
    histograma: ({ desde: number; hasta: number; n: number; crudas?: number } | number)[];
    por_confianza?: Record<string, number>;
    por_fuente?: Record<string, number>;
    /** Validación fuera de muestra (pliegue exterior, sin información futura). */
    validacion_resumen?: ValidacionResumen | null;
  };
  palancas: Palanca[];
  palancas_resumen?: {
    reactivar_full?: number; reactivar_full_con_ventas?: number; ventas_perdidas_dia?: number; ingreso_perdido_dia?: number;
    perdiendo_dinero?: number; perdida_dia?: number; pausadas_full_sin_stock_sin_odoo?: number; nota?: string;
  } | null;
  recomendaciones?: { filas: number; con_recomendacion: number; clases: Record<string, number>; por_confianza?: Record<string, number> } | null;
  serie_diaria: { fecha: string; cuenta: string; unidades: number | null; ingreso: number | null; visitas: number | null }[];
}

// ── Sobres de la API (api.py) ───────────────────────────────────────────────
export interface Pagina<T> {
  generado_at?: string | null;
  total: number;
  page: number;
  per_page: number;
  filas: T[];
  /** Conteos por canal y canal:cuenta para las píldoras (ignoran su propio filtro). */
  conteos?: Record<string, number>;
  /** Conteos por faceta («estado», «full», «fuente_costo», «razon»…), cada una ignorando su propio filtro. */
  facetas?: Record<string, Record<string, number>>;
  parametros?: Record<string, unknown>;
  sin_datos?: boolean;
}
