/**
 * publicaciones.ts — El vocabulario de la pestaña Omnicanal, y SOLO eso.
 *
 * Aquí se PRESENTA lo que el backend ya decidió. No se recalcula un margen, no
 * se deduce una oferta y no se infiere si una publicación está activa: las tres
 * cosas se miden canal por canal en `services/publicaciones_panel.py`, contra el
 * censo real, y llegan resueltas. Este archivo elige palabras y colores.
 *
 * Las tres trampas que este módulo existe para no repetir:
 *
 *  1. "Activa" NO es un valor único. ML dice `active`, TikTok `ACTIVATE`,
 *     Walmart `PUBLISHED`, Temu contesta códigos numéricos y Amazon distingue
 *     `BUYABLE` de `DISCOVERABLE`. Se pinta el estado NORMALIZADO; el crudo solo
 *     aparece cuando el backend no supo mapearlo (`desconocido`).
 *  2. Una oferta NO es "el campo de precio de campaña viene lleno". De 3,029
 *     filas con `price_sale` poblado, solo 609 traen un precio MENOR: pintar por
 *     campo-no-vacío mostraría 2,420 descuentos que no existen.
 *  3. `margen_pct: null` es "no se puede saber", no 0%. Amazon, TikTok, Temu y
 *     Walmart no tienen costo propio y el backend NO manda margen para ellos a
 *     propósito, para que nadie lo derive con la comisión equivocada.
 */

import type {
  EstadoNormalizado,
  MargenMotivo,
  OfertaEstado,
  RefrescoEstado,
  RefrescoPrecio,
} from "./types";

/** Tono visual de un estado. El color concreto lo pone el componente. */
export type TonoEstado = "vivo" | "vivo_matizado" | "ambar" | "gris";

export interface EstadoUI {
  label: string;
  tono: TonoEstado;
  /** Qué significa exactamente. Va en el tooltip; no se resume más. */
  ayuda: string;
}

/**
 * El vocabulario CERRADO del backend. Si aparece una llave nueva aquí sin que
 * el backend la mande, sobra; si el backend manda una que no está, cae en
 * `desconocido` y se muestra el crudo (que es justo lo que hay que ver).
 */
export const ESTADO_UI: Record<EstadoNormalizado, EstadoUI> = {
  activa: {
    label: "Activa",
    tono: "vivo",
    ayuda: "Se puede comprar AHORA.",
  },
  puede_estar_activa: {
    label: "Puede estar activa",
    tono: "vivo_matizado",
    ayuda:
      "El canal no distingue activo de inactivo, así que no se puede afirmar. "
      + "Es lo más cerca de la verdad que ese canal permite.",
  },
  no_comprable: {
    label: "No comprable",
    tono: "ambar",
    ayuda:
      "Existe y se ve en el catálogo, pero NO se vende. No es activa ni pausada: "
      + "es su propia cosa (Amazon DISCOVERABLE).",
  },
  pausada: { label: "Pausada", tono: "gris", ayuda: "Existe y está apagada." },
  en_revision: {
    label: "En revisión",
    tono: "gris",
    ayuda: "El canal la está revisando; todavía no vende.",
  },
  borrador: { label: "Borrador", tono: "gris", ayuda: "Nunca se publicó." },
  rechazada: {
    label: "Rechazada",
    tono: "gris",
    ayuda: "El canal la rechazó.",
  },
  cerrada: { label: "Cerrada", tono: "gris", ayuda: "Se dio de baja." },
  sin_estado: {
    label: "Sin estado",
    tono: "gris",
    ayuda:
      "El canal NO reporta estado para esta publicación. No significa que no "
      + "haya publicaciones: significa que no se sabe.",
  },
  desconocido: {
    label: "Desconocido",
    tono: "gris",
    ayuda:
      "Valor nuevo del canal que todavía nadie mapeó. Se muestra tal cual llegó "
      + "para que se pueda mapear, en vez de aplastarlo a otra cosa.",
  },
};

/** `estado` normalizado → ¿cuenta como viva para el filtro? (igual que el backend). */
export const ESTADOS_VIVOS: EstadoNormalizado[] = ["activa", "puede_estar_activa"];

/** Orden en que se ofrecen los estados en el filtro. */
export const ESTADOS_ORDEN: EstadoNormalizado[] = [
  "activa",
  "puede_estar_activa",
  "no_comprable",
  "pausada",
  "en_revision",
  "borrador",
  "rechazada",
  "cerrada",
  "sin_estado",
  "desconocido",
];

// ── Oferta ───────────────────────────────────────────────────────────────────

export const OFERTA_UI: Record<OfertaEstado, { label: string; ayuda: string }> = {
  con_oferta: {
    label: "Con oferta",
    ayuda: "El precio de campaña es MENOR que el de lista: hay descuento vivo.",
  },
  sin_oferta: {
    label: "Sin oferta",
    ayuda: "Se observó el precio de campaña y NO había promoción.",
  },
  desconocida: {
    label: "Sin observar",
    ayuda:
      "Nadie le ha preguntado al canal por el precio de campaña de esta "
      + "publicación. NO es lo mismo que 'sin oferta'.",
  },
};

/**
 * A partir de cuántos días una oferta deja de poder leerse como "de hoy".
 *
 * Quien escribe la observación (`precios_venta.py`) está dormido: la más nueva
 * en producción es del 21-ago. Una oferta sin su antigüedad al lado se lee como
 * vigente y puede llevar días muerta.
 */
export const OFERTA_DIAS_AMBAR = 2;

// ── Margen ───────────────────────────────────────────────────────────────────

/**
 * Por qué NO se puede saber el margen. Nunca se sustituye por 0 ni por un
 * guion mudo: el motivo es la mitad de la información.
 */
export const MOTIVO_TEXTO: Record<MargenMotivo | "desconocido", string> = {
  sin_costo_del_canal:
    "No hay costo de ESTE canal. Hoy solo Mercado Libre tiene motor de costos; "
    + "el costo de ML no se hereda a otro canal a propósito, porque su comisión "
    + "es distinta y el margen saldría falso.",
  sin_comision:
    "Falta el porcentaje de comisión de la categoría: sin él no hay cómo restar "
    + "lo que se lleva el canal.",
  sin_peso:
    "Falta el peso. Sin peso el costeo mete 0.5 kg de oficio y el fee de envío "
    + "sale del renglón barato de la tarifa: el margen quedaría optimista sin avisar.",
  sin_precio: "La publicación no tiene precio.",
  desconocido: "El backend no dijo por qué.",
};

/** Texto corto para el chip del censo. */
export const MOTIVO_CORTO: Record<MargenMotivo | "desconocido", string> = {
  sin_costo_del_canal: "sin costo del canal",
  sin_comision: "sin comisión",
  sin_peso: "sin peso",
  sin_precio: "sin precio",
  desconocido: "sin motivo",
};

// ── Confirmación del precio al abrir el cajón ────────────────────────────────

/**
 * Por qué NO se pudo confirmar el precio contra Mercado Libre. Sólo se usan
 * los estados que dejan `al_dia: false`; `ok`, `piso` y `sin_publicaciones` no
 * necesitan explicación porque no se avisa nada cuando todo está al día.
 */
const REFRESCO_MOTIVO: Partial<Record<RefrescoEstado, string>> = {
  apagado: "la confirmación en vivo está apagada",
  sin_token: "falta el token de esa cuenta de Mercado Libre",
  fallo: "Mercado Libre no contestó",
  timeout: "Mercado Libre tardó más de la cuenta",
  no_aplica: "no se pidió la confirmación",
};

/**
 * El porqué, en palabras de quien lo lee. Llamarlo SOLO cuando
 * `refresco.al_dia === false`.
 *
 * El caso mixto es el que importa y por eso va primero: `estado: "ok"` con
 * `sin_respuesta > 0` significa que a unas publicaciones ML sí les contestó y a
 * otras no. El aviso habla de las que NO, que son las que obligan a leer el
 * número con reserva.
 *
 * Todos los campos menos `al_dia` son opcionales en el contrato, así que aquí
 * no se asume que ninguno venga.
 */
export function motivoRefresco(r: RefrescoPrecio): string {
  if (r.sin_respuesta && r.sin_respuesta > 0) {
    return r.sin_respuesta === 1
      ? "una publicación no contestó"
      : `${r.sin_respuesta} publicaciones no contestaron`;
  }
  if (r.omitidas_tope && r.omitidas_tope > 0) {
    return "se alcanzó el tope de consultas por producto";
  }
  return (
    (r.estado && REFRESCO_MOTIVO[r.estado])
    ?? "Mercado Libre no confirmó el precio"
  );
}

// ── Canales y tiendas ────────────────────────────────────────────────────────

export const CANAL_LABEL: Record<string, string> = {
  mercado_libre: "Mercado Libre",
  amazon: "Amazon",
  tiktok: "TikTok",
  temu: "Temu",
  walmart: "Walmart",
  general: "WooCommerce",
};

export function labelCanal(canal: string): string {
  return CANAL_LABEL[canal] ?? canal;
}

export interface TiendaOpcion {
  /** `legacy_code`, tal cual lo espera el parámetro `tienda` del backend. */
  id: string;
  label: string;
  canal: string;
}

/**
 * Las tiendas por canal.
 *
 * Solo Mercado Libre opera dos cuentas; el resto tiene una y ahí "canal" y
 * "tienda" coinciden. Los `legacy_code` salen del contrato del backend y se
 * verificaron contra los datos: si alguno cambiara, el filtro devolvería 0
 * filas —no un error—, así que la lista se comprueba, no se supone.
 */
export const TIENDAS: TiendaOpcion[] = [
  { id: "BEKURA", label: "Kubera", canal: "mercado_libre" },
  { id: "SANCORFASHION", label: "San Corpe", canal: "mercado_libre" },
  { id: "AMAZON", label: "Amazon (San Corpe)", canal: "amazon" },
  { id: "KUBERA", label: "TikTok Shop", canal: "tiktok" },
  { id: "TEMU", label: "Temu", canal: "temu" },
  { id: "WALMART", label: "Walmart", canal: "walmart" },
];

export function tiendasDe(canal: string | null): TiendaOpcion[] {
  return canal ? TIENDAS.filter((t) => t.canal === canal) : TIENDAS;
}

export function labelTienda(id: string | null | undefined): string {
  if (!id) return "—";
  return TIENDAS.find((t) => t.id === id)?.label ?? id;
}

// ── Formato ──────────────────────────────────────────────────────────────────
//
// Se formatea, no se recalcula. La verdad del número vive en el backend.

const MXN = new Intl.NumberFormat("es-MX", {
  style: "currency",
  currency: "MXN",
  maximumFractionDigits: 2,
});

export function fmtMoneda(v: number | null | undefined, moneda = "MXN"): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  if (moneda && moneda !== "MXN") {
    return new Intl.NumberFormat("es-MX", {
      style: "currency",
      currency: moneda,
      maximumFractionDigits: 2,
    }).format(v);
  }
  return MXN.format(v);
}

/** Fracción (0.458) → "45.8 %". `null` NO se convierte en "0 %". */
export function fmtPct(v: number | null | undefined, decimales = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${(v * 100).toFixed(decimales)} %`;
}

/** Igual, pero con el signo delante: un margen negativo tiene que leerse negativo. */
export function fmtPctFirmado(v: number | null | undefined, decimales = 1): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const s = (v * 100).toFixed(decimales);
  return `${v > 0 ? "+" : ""}${s} %`;
}

export function fmtEntero(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return new Intl.NumberFormat("es-MX").format(v);
}

/** "hace 4.1 días" / "hace 3 h". El dato viene en días con decimal. */
export function fmtAntiguedad(dias: number | null | undefined): string {
  if (dias === null || dias === undefined || !Number.isFinite(dias)) return "sin fecha";
  if (dias < 1) {
    const h = Math.round(dias * 24);
    return h <= 1 ? "hace menos de 1 h" : `hace ${h} h`;
  }
  const d = dias < 10 ? dias.toFixed(1) : Math.round(dias).toString();
  return `hace ${d} d`;
}
