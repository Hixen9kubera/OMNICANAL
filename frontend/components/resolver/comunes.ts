/**
 * comunes.ts — las constantes y formateadores que comparten los DOS flujos de
 * resolución de costos.
 *
 *   · ResolverCostosModal      → packing-list-primero (cargas un xlsx y empatas
 *                                sus renglones contra los SKUs del contenedor)
 *   · ValidarPublicadosModal   → SKU-primero (seleccionas SKUs publicados en ML
 *                                y a cada uno se le busca su renglón)
 *
 * Existe por un incidente concreto: el tipo de cambio por defecto vivía DOS
 * veces —uno en `app/costos/page.tsx`, otro en `ResolverCostosModal`— y el
 * mismo costo en dólares daba dos costos en pesos según por dónde entrara la
 * captura. Un tercer default en el modal nuevo repetiría el incidente, así que
 * el número vive aquí y en ningún otro lado.
 */

/** Índigo del tab de Costos. */
export const COLOR = "#4F46E5";
/** Acento para focus rings y detalles. */
export const ACENTO = "#818CF8";

/** Tipo de cambio USD→MXN por defecto (backend: `packing_costos.TIPO_CAMBIO_DEFAULT`). */
export const TIPO_CAMBIO_DEFAULT = 19;

/**
 * Tarifa FIJA de flete en MXN por m³ — la fórmula que avaló Brandon el 21-ago
 * y la que usa el flujo SKU-primero: `flete = cbm_por_pieza × 7500`.
 *
 * Ojo: NO es la misma aritmética que `packing_costos.calcular()` del Resolver
 * viejo, que prorratea COSTO_CONTENEDOR_DEFAULT entre el CBM del archivo
 * completo. Con un contenedor de 70 m³ dan lo mismo; con un packing list
 * parcial, no. Por eso el modal nuevo escribe la tarifa a la vista.
 */
export const TARIFA_CBM = 7500;

/** Costo de un contenedor completo en MXN (prorrateo del Resolver viejo). */
export const COSTO_CONTENEDOR_DEFAULT = 525000;

/** Pesos mexicanos con dos decimales; `—` cuando no hay número. */
export const mxn = (v: number | null | undefined) =>
  v == null
    ? "—"
    : new Intl.NumberFormat("es-MX", {
        style: "currency",
        currency: "MXN",
        maximumFractionDigits: 2,
      }).format(v);

/** Estados de comparación del Resolver viejo (nuevo / igual / revisar). */
export const ESTADO_ESTILO: Record<string, { chip: string; label: string }> = {
  nuevo: { chip: "bg-sky-50 text-sky-700", label: "nuevo" },
  igual: { chip: "bg-emerald-50 text-emerald-700", label: "igual" },
  revisar: { chip: "bg-amber-50 text-amber-800", label: "revisar" },
};
