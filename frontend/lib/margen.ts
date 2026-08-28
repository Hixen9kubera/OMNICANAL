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
 * El umbral no es 1× a propósito: vender bajo costo existe (liquidar, error de
 * precio, promoción agresiva) y eso SÍ hay que verlo en rojo. Arriba del factor
 * ya no es una decisión comercial, es un dato mal capturado — y desde el 6-ago
 * sabemos por qué: el costo_producto de buena parte del catálogo es un precio
 * en dólares redondeado (×19), no un costo medido.
 *
 * DE 3× A 1.5× (Eduardo, 11-ago). El 3× dejaba pasar demasiado. Al destapar el
 * margen de 283 SKUs con el histórico de comisión, 56 de ellos quedaban en rojo
 * con un costo entre 1× y 3× el precio: creíbles a primera vista y sin nada que
 * avisara. TEC-1284-NEG-27" es el caso: se vende en $1,960 con un costo de
 * $4,229 (2.2×) y muestra −137.9%, que se lee como un producto ruinoso cuando
 * el problema está en el costo.
 *
 * A 1.5× las marcadas pasan de 45 a 96 en la ventana de 60 días (de 54 a 122 en
 * 120). El precio de bajarlo: una liquidación real a menos de dos tercios del
 * costo ahora sale marcada aunque el dato esté bien. Se aceptó ese falso
 * positivo — es más barato dudar de un costo correcto que dar por bueno uno
 * inventado, porque el segundo error termina en bajar una publicación sana.
 */

/** Arriba de este múltiplo, el costo deja de ser una cifra y pasa a ser un bug. */
export const FACTOR_COSTO_IMPLAUSIBLE = 1.5;

/**
 * ¿El costo de esta fila es tan alto frente al precio que no se puede creer?
 *
 * LA MARCA DE REVISADO GANA (Eduardo, 28-ago). El factor de 1.5× es un
 * DETECTOR: adivina que un costo está mal porque se ve raro. La marca de
 * `revisado_at` (migración 0032) es un HECHO: una persona abrió ese costeo,
 * lo comparó y firmó. Cuando existe el hecho, la adivinanza sobra — y peor,
 * se contradice con la etiqueta VALIDADO que la fila muestra al lado.
 *
 * Caso que lo destapó: ORG-0319-PLA, con VALIDADO y "COSTO DUDOSO" a la vez.
 * Es un producto que de verdad vende bajo costo (−127.7% con costo $166.14
 * contra un precio de $99.91), y el ámbar hacía dudar del dato en vez de
 * dejar ver el problema. Un costo revisado que sale en rojo no es un dato
 * sospechoso: es una pérdida confirmada, y así hay que pintarla.
 *
 * Ojo con lo que esto NO hace: no vuelve bueno un costo. Si alguien marca
 * como revisado un costeo malo, la marca calla la alerta — por eso la firma
 * (`revisado_por`) importa y por eso la marca se cae sola cuando el costo se
 * vuelve a tocar (`updated_at > revisado_at`).
 */
export function costoImplausible(
  precio: number | null | undefined,
  costoBase: number | null | undefined,
  revisadoAt?: string | null,
): boolean {
  if (revisadoAt) return false;
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

/* ── Margen BRUTO, sin IVA ──────────────────────────────────────────────────
   El precio de venta trae IVA; el costo de `costos_validados` NO (es mercancía
   + flete, sin un solo impuesto). Compararlos crudos cuenta como ganancia el
   IVA que solo se le pasa al SAT: infla ~14 puntos.

   El IVA de importación NO se le resta al costo a propósito — es ACREDITABLE,
   o sea que se recupera contra el IVA cobrado y por lo tanto nunca fue un
   costo. Solo se ajusta el precio. (Confirmado con Eduardo el 27-ago: los
   $525,000 del contenedor son sin IVA.)

   El punto de quiebre: un margen de 13.79% con la fórmula vieja es CERO con
   ésta. Todo lo que antes se pintaba por debajo de eso estaba perdiendo dinero
   en verde. */
export const IVA_RATE = 0.16;

/** Lo que de verdad entra a Kubera: el precio de lista sin el IVA del SAT. */
export function precioSinIva(precio: number): number {
  return precio / (1 + IVA_RATE);
}

/** Margen bruto en %, o null si falta un insumo. NO descuenta comisión ni envío. */
export function margenBruto(
  precio: number | null | undefined,
  costo: number | null | undefined,
): number | null {
  const p = Number(precio ?? 0), c = Number(costo ?? 0);
  if (!(p > 0) || !(c > 0)) return null;
  const neto = precioSinIva(p);
  return ((neto - c) / neto) * 100;
}

/**
 * EL COLOR DE UN MARGEN: verde si gana, rojo si pierde (Eduardo, 21-ago-2026).
 *
 * ANTES el umbral era 20%: `margen < 20 ? rojo : verde`. La intención era buena
 * —avisar de un margen delgado— pero el efecto era mentir sobre el signo. Un
 * producto que dejaba 15.5% se pintaba EXACTAMENTE igual que uno que perdía
 * 30%, y el rojo se lee como "este producto pierde dinero". En la vista del
 * 21-ago había cinco renglones positivos (2.9%, 5.1%, 8.6%, 15.5%, 15.6%)
 * pintados como pérdidas.
 *
 * El color contesta UNA pregunta —¿gana o pierde?— y la contesta bien. Qué tan
 * delgado es el margen ya lo dice la cifra, que está ahí al lado.
 *
 * El ámbar NO entra aquí: está reservado para el costo dudoso
 * (`costoImplausible`), que es una afirmación distinta —"no te fíes de este
 * número"— y mezclarla con la escala de bueno/malo las vuelve ilegibles a las
 * dos. Por eso quien llama evalúa el ámbar ANTES de pedir este tono.
 *
 * Vive aquí y no en cada página porque la regla estaba copiada en cuatro
 * lugares (tabla de Análisis, desglose por canal, popup de más vendidos y
 * Categorías) y ya habían empezado a divergir.
 *
 * ⚠ EL COLOR ES FIEL A LA CIFRA; LA CIFRA TODAVÍA NO LO ES. `margen_pct` y
 * `margen_neto_pct` comparan un precio CON IVA contra costos SIN IVA, así que
 * vienen ~14 puntos altos (ver `margenBruto` arriba: el equilibrio real cae en
 * 13.79% de esta escala, no en 0). Medido el 21-ago sobre el top 10 de 30 días:
 * MUE-0163-TEL muestra +3.8% y es −11.6%; TEC-0794-…GUANTS +1.9% y es −13.8%;
 * TEC-0552-NEG +8.7% y es −5.9%. Tres de diez cambian de signo, y esos tres se
 * pintan VERDES perdiendo dinero.
 *
 * Eduardo lo decidió así el 21-ago sabiendo esto: el umbral de 20% mentía sobre
 * el signo de TODOS los renglones entre 0 y 20, y esto miente sobre tres. La
 * cura no es otro umbral —el 20 le atinaba al equilibrio por accidente y por
 * seis puntos— sino descontarle el IVA al margen. Cuando eso pase, esta función
 * queda correcta sin tocarle una línea.
 */
export function tonoMargen(margen: number | null | undefined): string {
  if (margen == null || !Number.isFinite(Number(margen))) return "text-slate-300";
  return Number(margen) < 0 ? "text-red-500" : "text-emerald-600";
}
