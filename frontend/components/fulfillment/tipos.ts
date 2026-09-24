/**
 * Tipos de la pestaña FULLFILMENT.
 *
 * Todos son la forma EXACTA de lo que contesta el backend:
 *   · `GET /api/fulfillment/envios`      → `RespuestaEnvios` (Envíos y Análisis)
 *   · `GET /api/fulfillment/crear-full`  → `PropuestaFull`   (Crear FULL)
 *   · `GET /api/fulfillment/sku/{sku}`   → `FichaSku`        (la ventana del SKU)
 * Desde v0.556.0 ya no queda nada de diseño: lo que no tiene fuente se dice.
 *
 * La regla que atraviesa todos los tipos: `null` significa «no lo sabemos» y
 * NUNCA se colapsa a 0. Un cero es un dato; un null es un hueco, y la pantalla
 * los pinta distinto (ver `RAYADO` en `ui.tsx`).
 */

export type Canal = "meli" | "amazon" | "walmart";
export type FiltroCanal = Canal | "todos";

/** Las dos cuentas de ML se nombran SIEMPRE así. Nunca «Mercado Libre» a secas. */
export type Cuenta = "Kubera" | "San Corpe";
export type FiltroCuenta = Cuenta | "todas";

/** El rol del panel: `operador` se rotula KAM. Crear en Odoo es de admin (core/rbac.py). */
export type Rol = "admin" | "kam";

/** Un instante con zona. `aprox` = la hora es cuándo se OBSERVÓ, no cuándo ocurrió. */
export interface Instante {
  ts: string;          // ISO 8601 con zona
  aprox?: boolean;
  /** Sólo se sabe el DÍA (p. ej. la 1ª venta): se pinta sin hora. */
  dia?: boolean;
}

/**
 * Los dos pasos que ocurren ANTES de que exista la orden en Odoo. No los
 * registra ningún sistema todavía, así que en el rail no dicen "sin dato" (que
 * no le pide nada a nadie) sino **qué falta hacer y quién lo hace**.
 *
 * Historia corta: el 17-sep se quitaron por eso mismo —dos celdas rayadas en
 * cada renglón— y Brandon pidió devolverlas como AVISO: *"déjalos e indican qué
 * deben hacer"*. Desde v0.556.0 la solicitud nace en «Crear FULL»: con la tabla
 * `ops.fulfillment_solicitudes` (migración 0054) se guarda y estas dos etapas
 * pueden pasar a `ETAPAS` con su fecha.
 */
export const ETAPAS_POR_CAPTURAR = [
  { t: "Solicitado", corto: "Solicitado", accion: "se pide en Crear FULL",
    sub: "la lista de la semana",
    porque: "La solicitud original no se guarda en ningún sistema todavía: sin ella no se sabe cuánto se "
      + "pidió y la tasa de validado no existe. Nace en «Crear FULL»." },
  { t: "Validado", corto: "Validado", accion: "lo recorta Bodega",
    sub: "cuánto sí se puede surtir",
    porque: "El recorte de Bodega no queda en ningún lado: la cantidad de la orden de Odoo ya viene "
      + "recortada, así que tomarla de ahí pondría la tasa de validado en 100%." },
] as const;

export const ETAPAS = [
  // La fecha es la de la ORDEN DE VENTA: la teclea la KAM. El picking lo crea
  // OdooBot al confirmarla, así que su fecha no dice nada de una persona.
  { t: "Orden de venta", corto: "Orden", sub: "la crea la KAM en Odoo" },
  // `date_done` del OUT. Medido el 14-sep: en 16 de 43 envíos ML ya había
  // recibido ANTES de esta validación. No es la hora del camión y no se rotula así.
  { t: "Salida validada", corto: "Salida", sub: "en Odoo · no es el camión" },
  // ML: el primer AVISO de FULL de ML (webhook `fbm_stock_operations`) con
  // piezas de este envío ya vendibles. FBA sigue con el sync y viaja con `aprox`.
  { t: "Recibido", corto: "Recibido", sub: "llegada a FULL" },
  { t: "Activo", corto: "Activo", sub: "prende en FULL" },
  { t: "1ª venta", corto: "1ª venta", sub: "primera venta" },
] as const;

export type EstadoEnvio =
  | "abierta"            // la salida existe en Odoo y bodega no la ha validado
  | "salio"              // salida validada
  | "sinEnlazar"         // ML: la orden no trae número de envío
  | "fbaSinLectura"      // FBA: la app de Amazon no tiene permiso de Inbound (403)
  | "wfsSinLectura";     // WFS: la API responde, falta leerla

export interface Envio {
  /** id del picking OUT en Odoo. */
  id?: number;
  /** La orden de venta (S#####). */
  orden: string | null;
  /** El picking OUT (TEXCO/OUT/…). */
  salida?: string;
  almacen?: string | null;
  estado_odoo?: string;
  /** Número del envío en el marketplace, ya normalizado (sin «Envío #»). */
  envio: string | null;
  /** De dónde salió el número: lo tecleó la KAM en la referencia o está en el socio. */
  envio_origen?: "referencia" | "socio" | null;
  /** La referencia TAL CUAL la tecleó la KAM. */
  referencia?: string | null;
  socio?: string;
  canal: Canal;
  /** Sólo ML distingue cuenta; FBA es San Corpe; WFS y los creadores sin regla → null. */
  cuenta: Cuenta | null;
  /** Por qué tiene (o no tiene) esa cuenta. Se enseña: la regla depende de personas. */
  cuenta_regla?: string;
  kam: string | null;
  /** Piezas hechas. null = la salida no se ha validado (no es un cero). */
  piezas: number | null;
  pedidas: number | null;
  cajas: number | null;
  etapas: (Instante | null)[];
  estado: EstadoEnvio;
  n_skus?: number;
  /** Lo pedido que Odoo NO surtió (salida validada con renglones en 0). null = sin validar. */
  faltante_odoo?: number | null;
  /**
   * Cuántos SKUs del envío llegaron, se activaron y vendieron.
   * ML (`fuente: "avisos"`): suma de los avisos de FULL por SKU, topada a lo
   * enviado; con el envío CERRADO (10 días tras la salida) lo que falta es
   * `rechazadas`. FBA (`fuente: "sync"`): lo que vio el sync.
   */
  cobertura?: {
    fuente?: "avisos" | "sync";
    skus: number;
    llegaron: number;
    piezas_llegadas: number;
    activos: number;
    vendieron: number;
    completos?: number;
    piezas_enviadas?: number | null;
    piezas_vendidas?: number;
    cerrado?: boolean;
    /** null = el envío no ha cerrado: todavía no se sabe. */
    rechazadas?: number | null;
    cierre?: string | null;
    /** Avisos de FULL sin SKU legible (publicación con variantes) antes del cierre. */
    sin_sku?: { piezas: number; avisos: number };
  };
}

/** Un renglón de `GET /api/fulfillment/envios/{id}`. */
export interface LineaOdoo {
  sku: string;
  nombre: string;
  pedidas: number;
  /** null = la salida no se ha validado. */
  enviadas: number | null;
  /** Lo pedido que Odoo no surtió al validar. null = la salida no se ha validado. */
  faltante_odoo?: number | null;
  /** ML: piezas avisadas por ML (fbm_stock_operations) para este SKU en la ventana. */
  llegadas?: number;
  /** Lo que llegó de más: ML también baraja piezas entre sus bodegas. */
  llegadas_extra?: number;
  estado_llegada?: "completo" | "en_proceso" | "llegando" | "rechazo_parcial" | "rechazo_total" | null;
  rechazadas?: number;
  /** Primera y última tanda. */
  llegada?: string;
  llegada_ultima?: string;
  /** FBA: lo que vio el sync. */
  piezas_llegadas?: number;
  activacion?: string;
  primera_venta?: string;
}

export interface EnvioConLineas extends Envio {
  lineas: LineaOdoo[];
}

export interface Mediana { n: number; mediana: number | null; p90: number | null }

/**
 * Una semana de Análisis: la ISO de la SALIDA VALIDADA (hora de CDMX). Lo
 * recibido es lo que ML avisó DE ESOS envíos, llegue cuando llegue.
 */
export interface SemanaAnalisis {
  semana: string;
  anio: number;
  lunes: string;
  /** Odoo: salidas validadas esa semana. */
  envios: number;
  pedidas: number;
  enviadas: number;
  no_surtidas: number;
  sin_numero: number;
  /** Lo que se pudo MEDIR con los avisos de FULL (cuenta conocida, desde el 12-ago). */
  medidos: number;
  enviadas_medidas: number;
  recibidas: number;
  vendidas: number;
  cerrados: number;
  enviadas_cerradas: number;
  recibidas_cerradas: number;
  /** Envíos CERRADOS (10 días): lo enviado que no llegó. */
  no_recibidas: number;
  /** De ésas, las de envíos con avisos sin SKU (publicaciones con variantes). */
  dudosas: number;
  abiertos: number;
  /** Envíos abiertos: lo que falta todavía NO es rechazo. */
  en_recepcion: number;
  /** Sólo sobre los envíos cerrados de la semana. null = ninguno cerrado. */
  tasa: number | null;
  actual: boolean;
  salida_a_primera_llegada_dias: Mediana;
  salida_a_completo_dias: Mediana;
  orden_a_salida_dias: Mediana;
}

/** `semanas` de `GET /api/fulfillment/envios`, por grupo. */
export interface SerieSemanal {
  semanas: SemanaAnalisis[];
  /** Salidas abiertas hoy: todavía no salen, no son de ninguna semana. */
  por_validar: { envios: number; pedidas: number };
}

export interface StockCuenta {
  publicaciones: number; en_cero: number; con_stock: number; piezas: number; al: string | null;
}

/** El stock de HOY: FULL por cuenta y FBA (sólo disponible). null = kubera no contestó. */
export interface StockHoy {
  full: Partial<Record<Cuenta, StockCuenta>>;
  fba: { con_stock: number; piezas: number; al: string | null } | null;
  fuente: string;
}

export interface RespuestaEnvios {
  envios: Envio[];
  /** Llaves: "meli", "meli:Kubera", "meli:San Corpe", "meli:sin_asignar", "amazon", "walmart". */
  resumen: Record<string, unknown>;
  excluidas: { venta_amazon_mfn: number; otro: number };
  /** Mismas llaves que `resumen`. */
  semanas?: Record<string, SerieSemanal>;
  stock?: StockHoy | null;
  /** Si kubera contestó, y con qué ventanas se buscaron llegada y activación. */
  etapas_kubera?: {
    kubera: boolean; motivo?: string; desde_historia?: string; desde_avisos?: string;
    ventana_llegada_dias?: number; ventana_activacion_dias?: number; cierre_dias?: number;
  };
  generado: string;
  fuente: string;
  _cache?: { edad_s: number; ttl_s: number };
}

// ── Crear FULL ──────────────────────────────────────────────────────────────

/** Un SKU con venta FULL en la cuenta: los INSUMOS; la cantidad la calcula `proponer.ts`. */
export interface FilaPropuesta {
  sku: string;
  nombre: string | null;
  product_id: number | null;
  listing_id: string | null;
  url: string | null;
  situacion: string | null;
  v30: number;
  v7: number;
  ultima_venta: string | null;
  /** null = la publicación FULL no está en channel.listings: no se sabe (no es 0). */
  stock_full: number | null;
  en_camino: number;
  camino: string[];
  borrador: number;
  borradores: string[];
  /** Libre por almacén (TEXCO, TEXCO II). null = el SKU no existe en Odoo. */
  libre: Record<string, number> | null;
}

export interface BorradorFull {
  id: number;
  orden: string;
  cuenta: Cuenta | null;
  cuenta_regla: string;
  socio: string;
  kam: string | null;
  creada: string | null;
  referencia: string | null;
  origen: string | null;
  almacen: string | null;
  panel: boolean;
  piezas: number;
  skus: number;
  url: string;
}

export interface Interruptor {
  encendido: boolean;
  persistido: boolean;
  actualizado_por: string | null;
  motivo: string | null;
  actualizado_at: string | null;
}

export interface ParametrosFull {
  cobertura_dias: number;
  min_piezas: number;
  min_ventas_30: number;
  dejar_en_bodega: number;
}

export interface PropuestaFull {
  generado: string;
  semana: { semana: string; anio: number; lunes: string; domingo: string };
  parametros: ParametrosFull;
  almacenes: string[];
  cuentas: Record<Cuenta, FilaPropuesta[]>;
  borradores: BorradorFull[];
  zombis: { orden: string | null; salida: string | null; cuenta: Cuenta; creada: string; piezas: number }[];
  /** Todo lo que va hacia FULL por cuenta (no sólo de los SKUs con venta). */
  en_camino: Record<Cuenta, { piezas: number; skus: number }>;
  interruptor: Interruptor;
  fuente: string;
  _cache?: { edad_s: number; ttl_s: number };
}

/** Lo que contestan la vista previa y la creación. */
export interface ResultadoCrear {
  ok: boolean;
  accion?: "apagado" | "creada" | "ya_existia" | "nada_que_crear" | "sin_clave";
  motivo?: string | null;
  cuenta?: Cuenta;
  socio?: string;
  partes?: { almacen_id: number; almacen: string; piezas: number;
             lineas: { sku: string; nombre: string | null; cantidad: number }[] }[];
  recortes?: { sku: string; pedidas: number; van: number; porque: string }[];
  no_en_odoo?: string[];
  piezas_pedidas?: number;
  piezas?: number;
  ordenes?: { id: number; orden: string; estado: string; almacen: string; piezas: number;
              ya_existia: boolean; url?: string }[];
  solicitud_guardada?: boolean;
  interruptor?: Interruptor;
}

// ── La ficha del SKU ────────────────────────────────────────────────────────

export interface FichaSku {
  sku: string;
  nombre: string | null;
  generado: string;
  publicaciones: {
    cuenta: Cuenta; listing_id: string; url: string | null; situacion: string | null;
    is_fulfillment: boolean | null; logistic_type: string | null; stock_full: number;
    price: number | null; updated_at: string | null;
  }[];
  ventas_semanas: Record<Cuenta, { semana: string; lunes: string; unidades: number; actual: boolean }[]>;
  v30: Record<Cuenta, number>;
  llegadas: { cuenta: Cuenta; ts: string; tipo: string; piezas: number }[];
  envios: {
    id: number; orden: string | null; salida: string | null; envio: string | null; cuenta: Cuenta | null;
    estado_odoo: string; creada: string | null; validada: string | null;
    pedidas: number; enviadas: number | null; llegadas?: number; estado_llegada?: LineaOdoo["estado_llegada"];
    rechazadas?: number; llegada?: string; primera_venta?: string; cerrado?: boolean;
  }[];
  /** null = Odoo no contestó; `libre: null` = el SKU no está en Odoo. */
  odoo: { nombre: string | null; libre: Record<string, number> | null } | null;
  fuente: string;
}
