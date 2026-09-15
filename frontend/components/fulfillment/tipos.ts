/**
 * Tipos de la pestaña FULLFILMENT.
 *
 * Tienen la FORMA de lo que va a devolver el backend (`/api/fulfillment/envios/…`),
 * no la de la maqueta: hoy los llena `datosDiseno.ts` y el día que exista el API
 * se cambia la fuente sin tocar un solo componente.
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
}

/**
 * Las siete etapas de un envío, en orden. Cada una tiene fecha propia o es
 * `null`: ninguna se deduce de otra.
 */
export const ETAPAS = [
  { t: "Solicitado", sub: "lista de Andy" },
  { t: "Validado", sub: "Bodega aprobó" },
  { t: "Orden Odoo", sub: "picking outgoing" },
  // «Recolectado» es la VALIDACIÓN del picking en Odoo: la recolección física
  // no la guarda nadie. Por eso el sub-rótulo lo dice.
  { t: "Recolectado", sub: "picking validado" },
  { t: "Recibido", sub: "aviso del almacén" },
  { t: "Activo", sub: "prende en FULL" },
  { t: "1ª venta", sub: "primera venta" },
] as const;

export type EstadoEnvio =
  | "cerrado"            // el marketplace terminó de contar
  | "recepcion"          // salió de bodega, el almacén aún no cuenta: NO es rechazo
  | "sinEnlazar"         // la orden no trae número de envío: no hay tasa posible
  | "sinVenta"           // recibido y activo, sin primera venta todavía
  | "amazonSinLectura"   // FBA: la Inbound API no se consulta todavía
  | "wfs";               // WFS: no hay una sola orden

export interface Envio {
  /** Nombre del picking OUT en Odoo. */
  orden: string | null;
  /** Número del envío en el marketplace, ya normalizado (sin «Envío #»). */
  envio: string | null;
  canal: Canal;
  /** Sólo ML distingue cuenta; FBA es San Corpe; WFS aún no se sabe. */
  cuenta: Cuenta | null;
  kam: string | null;
  piezas: number | null;
  pedidas: number | null;
  /** Derivadas del factor de caja: se pintan con «~» y «estimadas». */
  cajas: number | null;
  etapas: (Instante | null)[];
  estado: EstadoEnvio;
  /** Sólo si el marketplace dio recibidas y rechazadas explícitas. */
  tasaPct?: number;
}

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
