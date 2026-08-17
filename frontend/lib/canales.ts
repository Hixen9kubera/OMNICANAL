/* Cómo se DIBUJAN los canales y las cuentas.
 *
 * Vive fuera de `analisis/page.tsx` desde el 14-ago: el popup de "Productos más
 * vendidos" usaba sus propios colores (iniciales BK/SC en índigo y celeste) y la
 * tabla los suyos (puntos celeste y violeta), así que el mismo SKU se veía de
 * dos maneras según dónde se mirara. Un color que significa "Bekura" tiene que
 * significar lo mismo en las dos pantallas o no significa nada.
 */

/** Punto de color por cuenta — el identificador visual de la tabla. */
export const CUENTA_DOT: Record<string, string> = {
  BEKURA: "bg-sky-500",
  SANCORFASHION: "bg-violet-500",
  AMAZON: "bg-amber-500",
};

/** Iniciales de cuenta para las etiquetas compactas. */
export const CUENTA_INI: Record<string, string> = {
  BEKURA: "BK", SANCORFASHION: "SC", AMAZON: "AMZ", GENERAL: "WOO",
};

/** Nombre largo, para los `title` y las tarjetas. */
export const CUENTA_NOMBRE: Record<string, string> = {
  BEKURA: "Kubera (BEKURA)", SANCORFASHION: "San Corpe (SANCORFASHION)",
  AMAZON: "Amazon", GENERAL: "WooCommerce",
};

export const CANAL_CORTO: Record<string, string> = {
  mercado_libre: "Meli", amazon: "Amazon", general: "Web",
};
