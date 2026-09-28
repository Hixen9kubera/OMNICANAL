// formato.ts — Cómo se PINTA el radar de precios.
//
// Aquí sólo se da formato. Toda la aritmética del radar (contribución, piso,
// techo, referencia, dirección, sugerido) vive en el backend
// (`services/radar_precios.py`); si un número se ve mal, el dato llegó mal.
//
// Regla que atraviesa el archivo: `null` es "sin dato" y NUNCA se pinta como 0.
// Por eso los formateadores devuelven `null` ante un valor ausente y es el
// componente quien decide mostrar la etiqueta "Sin dato".

import type {
  RadarClase,
  RadarDireccion,
  RadarEstadoContribucion,
  RadarExperiencia,
  RadarParametros,
} from "@/lib/api";

const ENTERO = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 0 });
const DOS = new Intl.NumberFormat("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const UNO = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 1 });

function esNumero(n: unknown): n is number {
  return typeof n === "number" && Number.isFinite(n);
}

/** $1,299 · −$41 · null si no hay dato. */
export function pesos(n: number | null | undefined, decimales: 0 | 2 = 0): string | null {
  if (!esNumero(n)) return null;
  const f = decimales === 2 ? DOS : ENTERO;
  const txt = `$${f.format(Math.abs(n))}`;
  return n < 0 ? `−${txt}` : txt;
}

/** 9.2 % (el número YA viene en 0–100). */
export function porcentaje(n: number | null | undefined, conSigno = false): string | null {
  if (!esNumero(n)) return null;
  const base = `${UNO.format(Math.abs(n))} %`;
  if (!conSigno) return n < 0 ? `−${base}` : base;
  if (n > 0) return `+${base}`;
  if (n < 0) return `−${base}`;
  return base;
}

/** Una fracción de negocio (0.10) como porcentaje legible (10 %). */
export function fraccionComoPct(n: number | null | undefined): string | null {
  if (!esNumero(n)) return null;
  return `${UNO.format(n * 100)} %`;
}

export function entero(n: number | null | undefined): string | null {
  if (!esNumero(n)) return null;
  return ENTERO.format(n);
}

export function decimal(n: number | null | undefined): string | null {
  if (!esNumero(n)) return null;
  return UNO.format(n);
}

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** 28-sep-2026 · 12:00 (hora local del navegador). */
export function fechaHora(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${d.getDate()}-${MESES[d.getMonth()]}-${d.getFullYear()} · ${hh}:${mm}`;
}

export function fechaCorta(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.getDate()}-${MESES[d.getMonth()]}-${d.getFullYear()}`;
}

/**
 * Donde la lista deja sus filtros para que el "Volver al radar" del detalle
 * regrese a la misma vista. Es comodidad del navegador, no estado del negocio.
 */
export const LLAVE_FILTROS_RADAR = "radar.filtros";

// ── Cuentas de Mercado Libre ────────────────────────────────────────────────
// El backend manda el `legacy_code`; en pantalla va el nombre corto del equipo.

export const CUENTAS_ML: { valor: string; etiqueta: string; corta: string }[] = [
  { valor: "BEKURA", etiqueta: "BEKURA", corta: "BEK" },
  { valor: "SANCORFASHION", etiqueta: "SANCOR", corta: "SAN" },
];

export function etiquetaCuenta(cuenta: string | null | undefined): string {
  if (!cuenta) return "—";
  const c = CUENTAS_ML.find((x) => x.valor === cuenta.toUpperCase());
  return c ? c.etiqueta : cuenta;
}

export function cortaCuenta(cuenta: string): string {
  const c = CUENTAS_ML.find((x) => x.valor === cuenta.toUpperCase());
  return c ? c.corta : cuenta.slice(0, 3).toUpperCase();
}

/** "94" → "C-94"; lo que ya traiga letras se deja como viene. */
export function etiquetaContenedor(c: string | null | undefined): string | null {
  if (!c) return null;
  return /^\d+$/.test(c.trim()) ? `C-${c.trim()}` : c;
}

// ── Dirección ───────────────────────────────────────────────────────────────
// Colores semánticos de la maqueta aprobada. El chip lleva SIEMPRE icono y
// texto: el color solo nunca es la única señal.

export interface MetaDireccion {
  etiqueta: string;
  /** Clases del chip (texto, fondo, borde). */
  chip: string;
  /** Clases de la tarjeta cuando está elegida. */
  tarjetaActiva: string;
  /** Clases de la caja del icono en la tarjeta. */
  icono: string;
  /** Color para SVG (línea del sugerido, borde de la banda). */
  color: string;
}

export const DIRECCIONES: RadarDireccion[] = [
  "subir",
  "bajar",
  "mantener",
  "caro_justificado",
  "no_competir",
  "sin_referencia",
];

export const META_DIRECCION: Record<RadarDireccion, MetaDireccion> = {
  subir: {
    etiqueta: "Subir",
    chip: "text-[#1849A9] bg-[#E6EEFE] border border-solid border-[#B6CCFA]",
    tarjetaActiva: "border-[#1849A9] bg-[#E6EEFE]",
    icono: "text-[#1849A9] bg-[#E6EEFE]",
    color: "#1849A9",
  },
  bajar: {
    etiqueta: "Bajar",
    chip: "text-[#9A3F07] bg-[#FDEFE3] border border-solid border-[#F5C49B]",
    tarjetaActiva: "border-[#9A3F07] bg-[#FDEFE3]",
    icono: "text-[#9A3F07] bg-[#FDEFE3]",
    color: "#9A3F07",
  },
  mantener: {
    etiqueta: "Mantener",
    chip: "text-[#475467] bg-[#EEF0F4] border border-solid border-[#D0D5DD]",
    tarjetaActiva: "border-[#475467] bg-[#EEF0F4]",
    icono: "text-[#475467] bg-[#EEF0F4]",
    color: "#475467",
  },
  caro_justificado: {
    etiqueta: "Caro justificado",
    chip: "text-[#1849A9] bg-white border border-solid border-[#1849A9]",
    tarjetaActiva: "border-[#1849A9] bg-[#F6F7FB]",
    icono: "text-[#1849A9] bg-white border border-solid border-[#1849A9]",
    color: "#1849A9",
  },
  no_competir: {
    etiqueta: "No competir en precio",
    chip: "text-[#5B21B6] bg-[#F3EEFE] border border-solid border-[#D4C5FB]",
    tarjetaActiva: "border-[#5B21B6] bg-[#F3EEFE]",
    icono: "text-[#5B21B6] bg-[#F3EEFE]",
    color: "#5B21B6",
  },
  sin_referencia: {
    etiqueta: "Sin referencia",
    chip: "text-[#4A5163] bg-white border border-dashed border-[#98A2B3]",
    tarjetaActiva: "border-[#4A5163] border-dashed bg-[#F6F7FB]",
    icono: "text-[#4A5163] bg-white border border-dashed border-[#98A2B3]",
    color: "#4A5163",
  },
};

/** Qué significa cada dirección, con los parámetros que mandó el backend. */
export function definicionDireccion(d: RadarDireccion, p: RadarParametros | null | undefined): string {
  const banda = fraccionComoPct(num(p?.banda_mantener)) ?? "la banda";
  const nMin = entero(num(p?.n_min_referencia));
  switch (d) {
    case "subir":
      return "Abajo del mercado: se sube hacia el techo, un paso a la vez.";
    case "bajar":
      return "Arriba del mercado sin premio de calidad que lo cubra.";
    case "mantener":
      return `En línea con el mercado (±${banda}), o con cobertura corta.`;
    case "caro_justificado":
      return "Arriba, pero el premio por Full y experiencia lo cubre.";
    case "no_competir":
      return "Igualar al mercado baja del piso: competir con ficha, Full o anuncios.";
    case "sin_referencia":
      return nMin
        ? `Sin comparables confiables (menos de ${nMin} rivales o captura vieja).`
        : "Sin comparables confiables todavía.";
  }
}

export function num(v: unknown): number | null {
  return esNumero(v) ? v : null;
}

// ── Clase, estado de la contribución, experiencia ───────────────────────────

export const ETIQUETA_CLASE: Record<RadarClase, string> = {
  exceso: "Exceso",
  normal: "Normal",
  recompra: "Recompra",
};

export function etiquetaClase(c: RadarClase | null | undefined): string | null {
  return c ? ETIQUETA_CLASE[c] ?? c : null;
}

/** Etiqueta de procedencia de un número: real / parcial / estimado / sin dato. */
export type Procedencia = RadarEstadoContribucion | "sin_dato";

export const META_PROCEDENCIA: Record<Procedencia, { etiqueta: string; clase: string }> = {
  real: { etiqueta: "Real", clase: "text-[#0B6B58] bg-[#E3F4EF] border border-transparent" },
  parcial: { etiqueta: "Parcial", clase: "text-[#3730A3] bg-[#EEF0FF] border border-transparent" },
  estimado: { etiqueta: "Estimado", clase: "text-[#8A5A00] bg-[#FEF3D6] border border-transparent" },
  sin_dato: { etiqueta: "Sin dato", clase: "text-[#4A5163] bg-white border border-dashed border-[#98A2B3]" },
};

export const META_EXPERIENCIA: Record<RadarExperiencia, { etiqueta: string; punto: string }> = {
  verde: { etiqueta: "Verde", punto: "bg-[#12805C]" },
  amarilla: { etiqueta: "Amarilla", punto: "bg-[#C98A00]" },
  roja: { etiqueta: "Roja", punto: "bg-[#B42318]" },
  sin_datos: { etiqueta: "Sin datos", punto: "bg-[#98A2B3]" },
};

/** Texto de la completitud del backend ("sin_dato", "estimado", …). */
export function etiquetaCompletitud(v: string | null | undefined): string | null {
  if (!v) return null;
  const t = v.toLowerCase();
  if (t === "sin_dato" || t === "sin dato") return "Sin dato";
  if (t === "estimado") return "Estimado";
  if (t === "real") return "Real";
  if (t === "parcial") return "Parcial";
  return v;
}

// ── Parámetros (decisión, no medición) ──────────────────────────────────────

export const ETIQUETA_PARAMETRO: Record<string, { etiqueta: string; tipo: "pct" | "dias" | "n" }> = {
  iva: { etiqueta: "IVA", tipo: "pct" },
  horizonte_exceso_dias: { etiqueta: "Exceso si la cobertura pasa de", tipo: "dias" },
  cobertura_min_para_bajar: { etiqueta: "Cobertura mínima para bajar (clase normal)", tipo: "dias" },
  m_seg_normal: { etiqueta: "Margen de seguridad del piso (normal)", tipo: "pct" },
  margen_reposicion: { etiqueta: "Margen de reposición del piso (recompra)", tipo: "pct" },
  premio_full: { etiqueta: "Premio por Full", tipo: "pct" },
  premio_experiencia_verde: { etiqueta: "Premio por experiencia verde", tipo: "pct" },
  premio_tope: { etiqueta: "Tope del premio de calidad", tipo: "pct" },
  banda_mantener: { etiqueta: "Banda de mantener (±)", tipo: "pct" },
  paso_max: { etiqueta: "Paso máximo por cambio", tipo: "pct" },
  n_min_referencia: { etiqueta: "Rivales mínimos para tener referencia", tipo: "n" },
  frescura_referencia_dias: { etiqueta: "Vigencia de la captura de mercado", tipo: "dias" },
  comision_estimada: { etiqueta: "Comisión estimada (sin dato real)", tipo: "pct" },
  dispersion_max: { etiqueta: "Rivales dispersos si el cuartil alto vale más de N veces el bajo", tipo: "n" },
  brecha_max: { etiqueta: "Brecha máxima contra la mediana (más = revisar término)", tipo: "pct" },
  promocion_min: { etiqueta: "En promoción si ML cobra al menos esto menos que la ficha", tipo: "pct" },
  recompra_n: { etiqueta: "SKUs marcados como recompra", tipo: "n" },
};

/** Por qué un SKU no tiene referencia de mercado (motivo del backend). */
export function motivoSinReferencia(motivo: string | null | undefined, nMin: number | null): string {
  switch (motivo) {
    case "sin_termino":
      return "Sin término de búsqueda";
    case "pocos_rivales":
      return nMin ? `Menos de ${nMin} rivales` : "Pocos rivales";
    case "captura_vieja":
      return "Captura de mercado vieja";
    case "rivales_dispersos":
      return "Rivales muy dispersos: revisar término";
    default:
      return "Sin comparables";
  }
}

/** Cobertura en días; sin ritmo de venta no hay cobertura que medir. */
export function textoCobertura(dias: number | null | undefined, ventasDia: number | null | undefined): string | null {
  if (typeof dias === "number" && Number.isFinite(dias)) {
    return dias > 365 ? "Más de 1 año" : `${ENTERO.format(dias)} días`;
  }
  if (ventasDia === 0) return "Sin ritmo de venta";
  return null;
}

export function valorParametro(clave: string, v: unknown): string {
  const meta = ETIQUETA_PARAMETRO[clave];
  if (v === null || v === undefined) return "Sin dato";
  if (typeof v === "boolean") return v ? "Sí" : "No";
  if (!esNumero(v) || !meta) return String(v);
  if (meta.tipo === "pct") return fraccionComoPct(v) ?? String(v);
  if (meta.tipo === "dias") return `${ENTERO.format(v)} días`;
  return ENTERO.format(v);
}

/** Cambio relativo de un precio a otro, SOLO para rotular ("−10 %"). */
export function cambioRelativo(de: number | null | undefined, a: number | null | undefined): string | null {
  if (!esNumero(de) || !esNumero(a) || de === 0) return null;
  return porcentaje(((a - de) / de) * 100, true);
}
