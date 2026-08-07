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
 * cifra se pinta MARCADA: en ámbar y con ⚠, no en el rojo/verde que se lee
 * como un hecho.
 *
 * CAMBIO (Eduardo, 6-ago): antes la celda se quedaba vacía. Ocultar el número
 * salía peor — un SKU marcado desaparecía del análisis y con él la sospecha de
 * que ALGO pasa ahí, aunque no sepamos cuánto. Ahora se muestra el margen y la
 * ganancia, con el aviso de que el costo puede estar mal: el lector decide.
 * Lo que sigue prohibido es pintarlos como si fueran ciertos.
 *
 * El umbral es 3× y no 1× a propósito: vender bajo costo existe (liquidar,
 * error de precio, promoción agresiva) y eso SÍ hay que verlo en rojo. Arriba
 * de 3× ya no es una decisión comercial, es un dato mal capturado — y desde el
 * 6-ago sabemos por qué: el costo_producto de buena parte del catálogo es un
 * precio en dólares redondeado (×19), no un costo medido.
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

/** El texto que acompaña a la cifra marcada, con los números del caso. */
export function avisoCostoImplausible(precio: number, costoBase: number): string {
  const veces = (costoBase / precio).toFixed(costoBase / precio >= 10 ? 0 : 1);
  return (
    `TÓMALO CON RESERVA: el costo capturado es ${veces}× el precio al que se vendió.\n` +
    `El margen y la ganancia de abajo SÍ se calculan, pero salen de ese costo, ` +
    `así que sirven de referencia y no como un hecho.\n` +
    `El problema está en el costo, no en la venta: verifícalo en Costos antes ` +
    `de mover el precio o dar de baja el producto.`
  );
}
