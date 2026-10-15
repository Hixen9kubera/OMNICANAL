// formato.ts — Cómo se escriben los números en PRICING (igual que el artifact).

export const pesos = (x: number | null | undefined) =>
  x == null ? "—" : "$" + Math.round(x).toLocaleString("es-MX");
export const pct1 = (x: number | null | undefined) => (x == null ? "—" : (x * 100).toFixed(1) + " %");
export const pctS = (x: number) => (x > 0 ? "+" : "") + Math.round(x * 100) + " %";
export const pzs = (x: number) =>
  x < 10 ? (Math.round(x * 10) / 10).toLocaleString("es-MX") : Math.round(x).toLocaleString("es-MX");

/** Fechas de los arreglos: el día `i` cuenta desde `d0` (mediodía, para no brincar de día por el huso). */
export function reloj(d0: string) {
  const D0 = new Date(d0 + "T12:00:00");
  const fecha = (i: number) => new Date(D0.getTime() + i * 864e5);
  const fFecha = (i: number) => fecha(i).toLocaleDateString("es-MX", { day: "numeric", month: "short" });
  return { D0, fecha, fFecha };
}

export type Reloj = ReturnType<typeof reloj>;
