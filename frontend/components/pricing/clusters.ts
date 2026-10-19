// clusters.ts — Los tres clusters de PRIORIDAD del Monitor de precios FULL
// (José, 19-oct-2026). Funciones puras: reciben las métricas en vivo de cada
// publicación (stock, ritmo, visitas y conversión de 30 días) y devuelven a qué
// cluster pertenece, o null.
//
// Se evalúan EN ORDEN y gana el primero que se cumple: una publicación clase A
// con 2,000 visitas, 0.5 % de conversión y 10 días de stock es «Se va a
// agotar», no «Mucha visita, poca venta». Solo entran publicaciones con stock.
//
//   agota   Se va a agotar        clase A o B y menos de 21 días de stock
//   fuga    Mucha visita, poca    600+ visitas en 30 días y conversión < 1 %
//           venta
//   joya    Convierte, pero       conversión de 3 % o más con menos de 1,200
//           nadie la ve           visitas
//
// CLASE ABC: las publicaciones ordenadas por venta de 30 días en pesos (piezas
// × precio de hoy); A = las que suman el primer 80 %, B = hasta el 95 %.
// La conversión solo cuenta con 100 visitas o más: abajo de eso es ruido.

export type Cluster = "agota" | "fuga" | "joya";

export const CLUSTERS: [Cluster, string, string][] = [
  ["agota", "Se va a agotar", "Clase A o B (las que hacen el 95 % de la venta) con menos de 21 días de stock FULL: reponer o embarcar"],
  ["fuga", "Mucha visita, poca venta", "600 visitas o más en 30 días y conversión menor a 1 %: revisar precio, fotos y ficha"],
  ["joya", "Convierte, pero nadie la ve", "Conversión de 3 % o más con menos de 1,200 visitas: Ads y promoción"],
];

export const UMBRAL = {
  diasAgota: 21,
  visAlta: 600,
  convBaja: 0.01,
  convAlta: 0.03,
  visTope: 1200,
  visMinConv: 100,
  claseA: 0.8,
  claseB: 0.95,
};

export interface EntradaCluster {
  stock: number;
  dias: number;
  vis30: number;
  /** Piezas ÷ visitas de 30 días; null sin visitas. */
  conv: number | null;
  /** Venta de 30 días en pesos. */
  venta30: number;
}

/** Un cluster por entrada (mismo orden), o null si no cae en ninguno. */
export function clusterizar(xs: EntradaCluster[]): (Cluster | null)[] {
  const total = xs.reduce((s, x) => s + x.venta30, 0);
  const clase = new Map<number, "A" | "B">();
  let acc = 0;
  xs.map((x, i) => [x.venta30, i] as const)
    .filter(([v]) => v > 0)
    .sort((a, b) => b[0] - a[0])
    .forEach(([v, i]) => {
      if (acc < total * UMBRAL.claseA) clase.set(i, "A");
      else if (acc < total * UMBRAL.claseB) clase.set(i, "B");
      acc += v;
    });
  return xs.map((x, i) => {
    if (!(x.stock > 0)) return null;
    const conv = x.vis30 >= UMBRAL.visMinConv ? x.conv : null;
    if (clase.has(i) && x.dias < UMBRAL.diasAgota) return "agota";
    if (x.vis30 >= UMBRAL.visAlta && conv != null && conv < UMBRAL.convBaja) return "fuga";
    if (conv != null && conv >= UMBRAL.convAlta && x.vis30 < UMBRAL.visTope) return "joya";
    return null;
  });
}
