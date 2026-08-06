/**
 * margen.ts — Cuándo un margen NO se puede creer.
 *
 * POR QUÉ EXISTE. Al encender el margen neto sobre datos de producción quedó a
 * la vista que el problema no es el cálculo sino los insumos: 119 SKUs con
 * venta en 60 días tienen un costo capturado MAYOR que el precio al que se
 * vendieron, y 32 de esos lo superan más de 3 veces — TEC-0406-AZL vende en
 * $269 con un costo de $30,058 (111×). El agregado se delata solo: la "pérdida"
 * implicada ($2.33M) es mayor que la venta ($1.94M), lo cual es imposible.
 *
 * EL RIESGO CONCRETO: alguien mira TEC-0393-ROS — 293 unidades vendidas, margen
 * −978% — y baja una publicación que quizá sea rentable. Un número rojo se lee
 * como un hecho; este no lo es.
 *
 * Por eso, cuando el costo supera al precio por más del factor de abajo, la
 * celda NO pinta un margen: avisa que el costo no es creíble y manda a
 * revisarlo. Es preferible una celda que dice "no sé" a una que miente con
 * precisión de un decimal.
 *
 * El umbral es 3× y no 1× a propósito: vender bajo costo existe (liquidar,
 * error de precio, promoción agresiva) y eso SÍ hay que verlo en rojo. Arriba
 * de 3× ya no es una decisión comercial, es un dato mal capturado.
 */

/** Arriba de este múltiplo, el costo deja de ser una cifra y pasa a ser un bug. */
export const FACTOR_COSTO_IMPLAUSIBLE = 3;

/** ¿El costo de esta fila es tan alto frente al precio que no se puede creer? */
export function costoImplausible(
  precio: number | null | undefined,
  costoBase: number | null | undefined,
): boolean {
  const p = Number(precio ?? 0);
  const c = Number(costoBase ?? 0);
  if (!(p > 0) || !(c > 0)) return false;
  return c > p * FACTOR_COSTO_IMPLAUSIBLE;
}

/** El texto que explica por qué no hay número, con las cifras del caso. */
export function avisoCostoImplausible(precio: number, costoBase: number): string {
  const veces = (costoBase / precio).toFixed(costoBase / precio >= 10 ? 0 : 1);
  return (
    `Costo no creíble: ${veces}× el precio al que se vendió.\n` +
    `No se muestra margen porque saldría un número falso — el problema está en ` +
    `el costo capturado, no en la venta.\n` +
    `Revísalo en Costos antes de tomar cualquier decisión de precio.`
  );
}
