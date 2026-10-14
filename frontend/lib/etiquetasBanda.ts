/* Etiquetas sobre una pista sin que se encimen, sea cual sea la distancia
   entre sus marcadores (banda «A precio de mercado» del cajón).

   Hasta v0.644.0 la banda decidía con un umbral en puntos de la escala («con
   22 de distancia ya caben centradas»), que no sabía cuánto mide cada texto ni
   que cerca de la orilla una etiqueta se alinea hacia dentro. ACC-0040-ROJ
   (14-oct-2026): Tú $253.00 al 66 % y Mercado $342.20 al 89 % pasaban el
   umbral, Mercado se recargaba a la derecha por estar pasada del 88 % y se
   montaba sobre Tú: «TÚ $253.00ADO $342.20».

   Ahora se trabaja en PIXELES con el ancho medido de cada etiqueta:
   1. cada una, centrada en su marcador y metida dentro de la pista;
   2. si chocan, el grupo que choca se abre a partes iguales alrededor de sus
      marcadores (nadie carga solo con el desplazamiento) sin salirse;
   3. si ni así caben en un renglón (pista angosta, montos largos), cada una se
      queda en su marcador y la que choca baja a otro renglón.
   Lógica pura: la prueba es `lib/pruebas/etiquetas_banda.prueba.cjs`. */

export interface Medida {
  /** Posición del marcador, 0–100 de la escala. */
  p: number;
  /** Ancho medido de la etiqueta, en px. */
  w: number;
}

export interface Colocada {
  /** Orilla izquierda de la etiqueta, en px desde el inicio de la pista. */
  left: number;
  /** Renglón: 0 el primero; los demás solo cuando no caben en uno. */
  fila: number;
}

/** Separación mínima entre dos etiquetas del mismo renglón, en px. */
export const AIRE_PX = 8;

const dentro = (left: number, w: number, ancho: number) =>
  Math.max(0, Math.min(left, ancho - w));

export function acomodarEtiquetas(medidas: Medida[], ancho: number, aire = AIRE_PX): Colocada[] {
  const n = medidas.length;
  if (!n) return [];
  const ideal = medidas.map(({ p, w }) => dentro((p / 100) * ancho - w / 2, w, ancho));
  const orden = medidas.map((_, i) => i).sort((a, b) => medidas[a]!.p - medidas[b]!.p || a - b);
  const total = medidas.reduce((s, m) => s + m.w, 0) + aire * (n - 1);

  if (total <= ancho) {
    // Grupos contiguos: al chocar dos se funden, y el grupo se coloca donde el
    // desplazamiento de sus etiquetas suma cero (el promedio), sin salirse.
    type Grupo = { idx: number[]; off: number[]; left: number; w: number };
    const colocar = (g: Grupo) => {
      const media = g.idx.reduce((s, i, k) => s + ideal[i]! - g.off[k]!, 0) / g.idx.length;
      g.left = dentro(media, g.w, ancho);
    };
    const grupos: Grupo[] = orden.map((i) => ({ idx: [i], off: [0], left: ideal[i]!, w: medidas[i]!.w }));
    let fundio = true;
    while (fundio) {
      fundio = false;
      for (let k = 0; k + 1 < grupos.length; k++) {
        const a = grupos[k]!;
        const b = grupos[k + 1]!;
        if (a.left + a.w + aire <= b.left + 0.01) continue;
        const desde = a.w + aire;
        const g: Grupo = {
          idx: [...a.idx, ...b.idx],
          off: [...a.off, ...b.off.map((o) => o + desde)],
          left: 0,
          w: a.w + aire + b.w,
        };
        colocar(g);
        grupos.splice(k, 2, g);
        fundio = true;
        break;
      }
    }
    const out: Colocada[] = new Array(n);
    for (const g of grupos) g.idx.forEach((i, k) => { out[i] = { left: g.left + g.off[k]!, fila: 0 }; });
    return out;
  }

  // No caben en un renglón: cada una en su marcador, y la que choca baja.
  const filas: { l: number; r: number }[][] = [];
  const out: Colocada[] = new Array(n);
  for (const i of orden) {
    const l = ideal[i]!;
    const r = l + medidas[i]!.w;
    let f = 0;
    while (filas[f]?.some((x) => l < x.r + aire && x.l < r + aire)) f++;
    (filas[f] ||= []).push({ l, r });
    out[i] = { left: l, fila: f };
  }
  return out;
}
