/**
 * coloresVariante.ts — la muestra de color de 9×9 px del rail de variantes.
 *
 * POR QUÉ NO SE REUSA `colorMap`
 * ------------------------------
 * La lista del Publicador ya pasa un `colorMap`, pero ése es de CANALES (el
 * amarillo de ML, el naranja de Temu). Lo que el rail necesita es traducir el
 * valor del atributo de la variante —"Café", "Rosa"— a un color de pantalla.
 * No existía.
 *
 * EL VOCABULARIO ES EL REAL, no uno inventado
 * -------------------------------------------
 * Censo de `wp_postmeta` sobre las 7,477 variaciones (10-sep-2026):
 *
 *   attribute_color     5,701   ← el eje dominante, y el único que es un color
 *   attribute_talla     3,442
 *   attribute_variante  1,114   "VER-XXL": color y talla pegados en un campo
 *   attribute_modelo    1,047
 *   medida · cantidad · voltaje · lado   (95 entre todos)
 *
 * Tres formas que hay que aguantar y que un mapa ingenuo rompe:
 *
 *   1. COMPUESTOS — "Blanco / Azul" (47), "Negro / Rosa" (38), "Negro / Gris"
 *      (39)… Son dos colores de verdad, así que la muestra sale partida en
 *      diagonal en vez de elegir uno y mentir.
 *   2. NO-COLORES — "Estampado" (291), "Multicolor" (156), "Metálico" (160).
 *      No tienen hex; llevan muestra propia.
 *   3. EJES QUE NO SON COLOR — una talla "XL" o un modelo "Flo" no se pintan:
 *      devuelven `null` y el rail muestra el renglón sin muestra. Pintar "XL"
 *      de gris haría creer que ES un color gris.
 */

/** Nombre en minúsculas y sin acentos → hex. */
const HEX: Record<string, string> = {
  negro: "#111827",
  blanco: "#f8fafc",
  gris: "#9ca3af",
  "gris oscuro": "#4b5563",
  "gris perla": "#d1d5db",
  "gris claro": "#d1d5db",
  plateado: "#c0c4cc",
  plata: "#c0c4cc",
  dorado: "#d4af37",
  oro: "#d4af37",
  azul: "#2563eb",
  "azul marino": "#1e3a5f",
  "azul claro": "#7dd3fc",
  "azul cielo": "#7dd3fc",
  celeste: "#7dd3fc",
  turquesa: "#2dd4bf",
  verde: "#16a34a",
  "verde limon": "#a3e635",
  "verde militar": "#4d5d3a",
  "verde menta": "#86efac",
  rojo: "#dc2626",
  vino: "#7f1d1d",
  guinda: "#7f1d1d",
  rosa: "#ec4899",
  "rosa palo": "#f5c2c7",
  fucsia: "#d946ef",
  morado: "#7c3aed",
  lila: "#c4b5fd",
  violeta: "#8b5cf6",
  amarillo: "#facc15",
  mostaza: "#d4a017",
  naranja: "#f97316",
  cafe: "#6f4e37",
  chocolate: "#5b3a29",
  camel: "#c19a6b",
  beige: "#e8dcc8",
  crema: "#f5efe0",
  marfil: "#f7f3e8",
  nude: "#e3bc9a",
  durazno: "#fcd5b5",
  coral: "#fb7185",
  transparente: "#e2e8f0",
  natural: "#e8dcc8",
};

/** Valores que SON un acabado, no un color plano. */
const ESPECIAL: Record<string, string> = {
  // Cada uno lleva su propio degradado: un gris liso los volvería
  // indistinguibles entre sí y de un gris de verdad.
  multicolor: "conic-gradient(#ef4444,#f59e0b,#22c55e,#3b82f6,#a855f7,#ef4444)",
  estampado: "repeating-linear-gradient(45deg,#94a3b8 0 3px,#e2e8f0 3px 6px)",
  metalico: "linear-gradient(135deg,#e5e7eb 0%,#9ca3af 45%,#f3f4f6 55%,#6b7280 100%)",
  tornasol: "linear-gradient(135deg,#a78bfa,#38bdf8,#34d399)",
};

function limpiar(v: string): string {
  return v
    .trim()
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "") // café → cafe
    .replace(/\s+/g, " ");
}

export interface MuestraColor {
  /** Valor listo para `style.background`: hex, degradado o los dos partidos. */
  background: string;
  /** El texto tal como lo guarda WooCommerce ("Negro / Rosa"). */
  etiqueta: string;
}

/**
 * La muestra de un valor de atributo, o `null` si no es un color.
 *
 * `null` NO es un fallo: es la respuesta correcta para una talla o un modelo.
 * Quien llama debe omitir la muestra, no dibujar un cuadrito gris.
 */
export function muestraColor(valor: string | null | undefined): MuestraColor | null {
  if (!valor) return null;
  const etiqueta = valor.trim();
  const base = limpiar(etiqueta);
  if (!base) return null;

  if (ESPECIAL[base]) return { background: ESPECIAL[base], etiqueta };
  if (HEX[base]) return { background: HEX[base], etiqueta };

  // Compuestos: "Blanco / Azul", "Negro-Rosa". Se parten en diagonal para no
  // tener que elegir cuál de los dos representa a la variante.
  const partes = base.split(/\s*[/|+]\s*/).filter(Boolean);
  if (partes.length === 2) {
    const a = HEX[partes[0]] ?? ESPECIAL[partes[0]];
    const b = HEX[partes[1]] ?? ESPECIAL[partes[1]];
    if (a && b) return { background: `linear-gradient(135deg, ${a} 0 50%, ${b} 50% 100%)`, etiqueta };
    if (a) return { background: a, etiqueta };
    if (b) return { background: b, etiqueta };
  }

  // Última red: "azul rey", "verde botella" — el adjetivo no está en el mapa
  // pero el color base sí, y acertar el tono aproximado es mejor que no pintar.
  for (const palabra of base.split(" ")) {
    if (HEX[palabra]) return { background: HEX[palabra], etiqueta };
  }
  return null;
}

/**
 * Parte el `nombre` de una variante en muestra de color y etiqueta.
 *
 * ⚠️ LA ETIQUETA ES EL `nombre` COMPLETO, y eso NO es pereza.
 *
 * Los mockups rotulan el rail "Café", "Rosa", "Negro" — pero ahí el único eje
 * es el color, así que el nombre completo YA es "Café". En el catálogo real hay
 * familias con dos ejes, y quedarse con el color las vuelve indistinguibles.
 * Medido el 10-sep-2026 sobre `ACC-0424` (zapatilla de ballet):
 *
 *     ACC-0424-ROS-39   "Rosa / 39"
 *     ACC-0424-ROS-38   "Rosa / 38"      ← ocho renglones que dirían "Rosa"
 *     ACC-0424-ROS-37   "Rosa / 37"
 *
 * El eje que las separa es la TALLA. Y no es un caso raro: `attribute_talla`
 * aparece en 3,442 de las 7,477 variaciones.
 *
 * El color sólo decide la MUESTRA. `muestraColor` ya resuelve el compuesto de
 * dos ("Negro / Rosa" → diagonal) y el par color+talla ("Rosa / 39" → rosa,
 * porque "39" no es un color), así que se le pasa la cadena entera; si eso
 * falla, se recorre parte por parte.
 */
export function partesVariante(nombre: string | null | undefined): {
  color: MuestraColor | null;
  /** El `nombre` completo: es lo único que distingue a las hermanas. */
  etiqueta: string;
} {
  const texto = (nombre ?? "").trim();
  if (!texto) return { color: null, etiqueta: "" };

  const entera = muestraColor(texto);
  if (entera) return { color: entera, etiqueta: texto };

  for (const p of texto.split(/\s*\/\s*/).filter(Boolean)) {
    const m = muestraColor(p);
    if (m) return { color: m, etiqueta: texto };
  }
  // Ni una parte es color: la familia va por talla, modelo o voltaje. Se rotula
  // igual, sin muestra — que es la respuesta honesta.
  return { color: null, etiqueta: texto };
}
