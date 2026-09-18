/**
 * Tipos de la pestaña FULLFILMENT.
 *
 * `Envio`, `RespuestaEnvios` y `ResumenGrupo` son la forma EXACTA de
 * `GET /api/fulfillment/envios` (backend/services/fulfillment_envios.py). Lo que
 * todavía es diseño (planeación, ficha de SKU, variaciones) usa los mismos tipos
 * con los datos de `datosDiseno.ts`.
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

export type Rol = "admin" | "kam";

/** Un instante con zona. `aprox` = la hora es cuándo se OBSERVÓ, no cuándo ocurrió. */
export interface Instante {
  ts: string;          // ISO 8601 con zona
  aprox?: boolean;
  /** Sólo se sabe el DÍA (p. ej. la 1ª venta): se pinta sin hora. */
  dia?: boolean;
}

/**
 * Las siete etapas de un envío, en orden. Cada una tiene fecha propia o es
 * `null`: ninguna se deduce de otra.
 */
/**
 * Los dos pasos que ocurren ANTES de que exista la orden en Odoo. No los
 * registra ningún sistema todavía, así que en el rail no dicen "sin dato" (que
 * no le pide nada a nadie) sino **qué falta hacer y quién lo hace**.
 *
 * Historia corta: el 17-sep se quitaron por eso mismo —dos celdas rayadas en
 * cada renglón— y Brandon pidió devolverlas como AVISO: *"déjalos e indican qué
 * deben hacer"*. Andy es quien puede levantar la solicitud, así que el rail se
 * lo dice por su nombre.
 *
 * El día que se capturen dejan de ser aviso y pasan a `ETAPAS` con su fecha.
 */
export const ETAPAS_POR_CAPTURAR = [
  { t: "Solicitado", corto: "Solicitado", accion: "lo pide Andy",
    sub: "la lista del martes",
    porque: "Nadie guarda la lista de Andy: sin ella no se sabe cuánto se pidió, y la tasa de "
      + "validado no existe. Se captura en Planeación semanal." },
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
  // NO es el aviso de ML (su API no tiene envíos a Full): es la subida de
  // stock_full que ve el sync cada 15 min, o sea cuando las piezas ya se pueden
  // vender. Por eso viaja con `aprox` y se rotula "observado".
  { t: "Recibido", corto: "Recibido", sub: "llegada observada" },
  { t: "Activo", corto: "Activo", sub: "prende en FULL" },
  { t: "1ª venta", corto: "1ª venta", sub: "primera venta" },
] as const;

export type EstadoEnvio =
  // ── los que devuelve el backend ──
  | "abierta"            // la salida existe en Odoo y bodega no la ha validado
  | "salio"              // ML: salida validada; las recepciones aún no se leen
  | "sinEnlazar"         // ML: la orden no trae número de envío → no hay tasa posible
  | "fbaSinLectura"      // FBA: la app de Amazon no tiene permiso de Inbound (403)
  | "wfsSinLectura"      // WFS: la API responde, falta leerla
  // ── solo en la pantalla de Variaciones (ejemplos de diseño) ──
  | "cerrado" | "recepcion" | "sinVenta" | "amazonSinLectura" | "wfs";

export interface Envio {
  /** id del picking OUT en Odoo (solo en datos reales). */
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
  /** Derivadas del factor de caja: se pintan con «~» y «estimadas». null = sin factor. */
  cajas: number | null;
  etapas: (Instante | null)[];
  estado: EstadoEnvio;
  n_skus?: number;
  /** Cuántos SKUs del envío ya llegaron, se activaron y vendieron (observado). */
  cobertura?: {
    skus: number;
    llegaron: number;
    /** Piezas que ENTRARON a FULL en la ventana: puede incluir otro envío del mismo SKU. */
    piezas_llegadas: number;
    activos: number;
    vendieron: number;
  };
  /** Sólo si el marketplace dio recibidas y rechazadas explícitas (diseño). */
  tasaPct?: number;
}

/** Un renglón de `GET /api/fulfillment/envios/{id}`. */
export interface LineaOdoo {
  sku: string;
  nombre: string;
  pedidas: number;
  /** null = la salida no se ha validado. */
  enviadas: number | null;
  /** Observado en kubera tras la salida; ausente = no se vio nada. */
  llegada?: string;
  piezas_llegadas?: number;
  activacion?: string;
  primera_venta?: string;
}

export interface EnvioConLineas extends Envio {
  lineas: LineaOdoo[];
}

export interface ResumenGrupo {
  salidas: number;
  hechas: number;
  abiertas: number;
  piezas_enviadas: number;
  piezas_pedidas_hechas: number;
  piezas_abiertas: number;
  sin_numero: number;
  desde: string | null;
  hasta: string | null;
  /** 7 posiciones, lunes = 0, en hora de CDMX. */
  dias_orden: number[];
  dias_validacion: number[];
  orden_a_validacion_dias: { n: number; mediana: number | null; p90: number | null };
}

export interface RespuestaEnvios {
  envios: Envio[];
  /** Llaves: "meli", "meli:Kubera", "meli:San Corpe", "meli:sin_asignar", "amazon", "walmart". */
  resumen: Record<string, ResumenGrupo>;
  excluidas: { venta_amazon_mfn: number; otro: number };
  /** Si kubera contestó, y con qué ventanas se buscaron llegada y activación. */
  etapas_kubera?: {
    kubera: boolean; motivo?: string; desde_historia?: string;
    ventana_llegada_dias?: number; ventana_activacion_dias?: number;
  };
  generado: string;
  fuente: string;
  _cache?: { edad_s: number; ttl_s: number };
}

/** Renglón de ejemplo de la pantalla de Variaciones. */
export interface LineaEnvio {
  sku: string;
  nombre: string;
  publicacion: string | null;   // MLM / ASIN
  solicitadas: number | null;
  validadas: number | null;
  enviadas: number;
  /** Explícitos del marketplace. null = no han llegado. */
  recibidas: number | null;
  rechazadas: number | null;
  cajas: number | null;
}

export type EstadoRenglonPlan = "aprobado" | "recorte" | "pendiente";

export interface RenglonPlan {
  sku: string;
  nombre: string;
  destino: "meli_bekura" | "meli_sank" | "fba" | "wfs" | "drop";
  libre: number | null;         // free_qty por almacén
  pidio: number;
  bodega: number | null;        // null = Bodega no ha revisado
  ia: number | null;
  estado: EstadoRenglonPlan;
}

export interface DiaSemana {
  dia: "lun" | "mar" | "mié" | "jue" | "vie" | "sáb";
  nombre: string;
  ordenes: number;
  salidas: number;
}

export interface CuentaFull {
  cuenta: Cuenta;
  enCero: number;
  publicaciones: number;
  piezas: number;
  conStock: number;
}
