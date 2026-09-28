/**
 * Formatos. Se formatea, no se recalcula: la verdad del número vive en el
 * pipeline (`sandbox_precios/`). Mismo criterio que `frontend/lib/publicaciones.ts`:
 * `null` NUNCA se convierte en "0" ni en "0 %".
 */

const MXN = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 2 });
const MXN0 = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 0 });
const ENTERO = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 0 });

const ok = (v: number | null | undefined): v is number => v !== null && v !== undefined && Number.isFinite(v);

/** $1,234.5 — sin centavos si es entero. */
export function pesos(v: number | null | undefined, vacio = "—"): string {
  if (!ok(v)) return vacio;
  return (Math.abs(v % 1) < 0.005 ? MXN0 : MXN).format(v);
}

/** $12.9 k / $1.2 M para KPIs; las tablas usan `pesos`. */
export function pesosCorto(v: number | null | undefined, vacio = "—"): string {
  if (!ok(v)) return vacio;
  const a = Math.abs(v);
  if (a >= 1_000_000) return `${v < 0 ? "−" : ""}$${(a / 1_000_000).toFixed(a >= 10_000_000 ? 0 : 1)} M`;
  if (a >= 10_000) return `${v < 0 ? "−" : ""}$${(a / 1000).toFixed(a >= 100_000 ? 0 : 1)} k`;
  return pesos(v, vacio);
}

export function entero(v: number | null | undefined, vacio = "—"): string {
  return ok(v) ? ENTERO.format(v) : vacio;
}

/** Con decimales sólo cuando el número es chico (unidades/día 0.4 no es 0). */
export function cifra(v: number | null | undefined, vacio = "—"): string {
  if (!ok(v)) return vacio;
  const a = Math.abs(v);
  const dec = a >= 100 ? 0 : a >= 10 ? 1 : 2;
  return v.toLocaleString("es-MX", { minimumFractionDigits: 0, maximumFractionDigits: dec });
}

/** Fracción (0.138) → "13.8 %". */
export function pct(v: number | null | undefined, dec = 1, vacio = "—"): string {
  if (!ok(v)) return vacio;
  return `${(v * 100).toFixed(dec)} %`;
}

/** Igual con signo: un margen negativo se tiene que LEER negativo. */
export function pctFirmado(v: number | null | undefined, dec = 1, vacio = "—"): string {
  if (!ok(v)) return vacio;
  const s = (v * 100).toFixed(dec);
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${s.replace("-", "")} %`;
}

export function pesosFirmado(v: number | null | undefined, vacio = "—"): string {
  if (!ok(v)) return vacio;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${pesos(Math.abs(v))}`;
}

export function cifraFirmada(v: number | null | undefined, vacio = "—"): string {
  if (!ok(v)) return vacio;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${cifra(Math.abs(v))}`;
}

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const FMT_CDMX = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Mexico_City", year: "numeric", month: "numeric", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hour12: false,
});

function partes(iso: string) {
  return Object.fromEntries(FMT_CDMX.formatToParts(new Date(iso)).map((p) => [p.type, p.value]));
}

/**
 * «28 sep». Una fecha PELONA ("2026-09-28") se lee como día, no como medianoche
 * UTC: en CDMX eso sería el 27 a las 18:00 (la trampa documentada en
 * `analisis/metricas/page.tsx`).
 */
export function dia(iso: string | null | undefined, vacio = "—"): string {
  if (!iso) return vacio;
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) {
    const [, m, d] = iso.split("-").map(Number);
    return `${d} ${MESES[m - 1]}`;
  }
  const p = partes(iso);
  return `${Number(p.day)} ${MESES[Number(p.month) - 1]}`;
}

/** «28 sep 14:05» en hora de CDMX. */
export function diaHora(iso: string | null | undefined, vacio = "—"): string {
  if (!iso) return vacio;
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return dia(iso);
  const p = partes(iso);
  return `${Number(p.day)} ${MESES[Number(p.month) - 1]} ${p.hour === "24" ? "00" : p.hour}:${p.minute}`;
}

/** Días completos entre un instante y ahora (para avisos de frescura). */
export function diasDesde(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00` : iso);
  if (!Number.isFinite(t)) return null;
  return Math.floor((Date.now() - t) / 86_400_000);
}

/** Tono de un margen: rojo si pierde, ámbar bajo el piso, verde si gana (lib/margen.ts:159). */
export function tonoMargen(m: number | null | undefined, piso = 0.12): string {
  if (!ok(m)) return "text-slate-400";
  if (m < 0) return "text-rose-600";
  if (m < piso) return "text-amber-700";
  return "text-emerald-600";
}
