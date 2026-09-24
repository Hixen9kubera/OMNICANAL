/**
 * combinados.ts — cómo se NOMBRA y se PINTA un envío combinado, igual en la
 * pestaña de Automatización, en la ventana "Guías del día" y en su Excel.
 *
 * ENVÍO COMBINADO: dos o más ventas del MISMO canal que comparten guía. Temu las
 * junta cuando el mismo comprador compra varias veces a la misma dirección antes
 * de que salga el envío: salen en UNA caja con UNA etiqueta.
 *
 * POR QUÉ YA NO HAY LETRAS (hallazgo del 15-sep-2026)
 * ---------------------------------------------------
 * La pestaña armaba los grupos con la bitácora de 30 días y los nombraba A, B,
 * C… por su lugar en esa lista; el Excel los arma por DÍA. Con los datos reales,
 * S38448+S38503 era "Combinado B" cian en la pestaña y "A" lavanda en el Excel
 * del 13-sep: un empacador con la hoja en la mano y el panel abierto leía "B"
 * y pensaba en otra caja. Y las letras de la pestaña se recorrían solas cuando
 * un grupo viejo salía de la ventana de 30 días.
 *
 * Ahora:
 *   · el NOMBRE es el final de la guía ("…2532"), que es lo que se lee en la
 *     etiqueta pegada en la caja y no depende de ninguna lista;
 *   · el COLOR sale de la guía misma (FNV-1a de `canal|guía`), y sólo si dos
 *     grupos del MISMO día chocan, el más nuevo toma el siguiente libre —el
 *     Excel del día hace exactamente eso con sus grupos—.
 *
 * ⚠️ GEMELO de backend/services/guias_del_dia.py (PALETA, norm_guia, clave_guia,
 * codigo_guia, indice_preferido, asignar_colores). `test_guias_dia.py` corre
 * este archivo con node y compara, caso por caso, contra el de Python: si se
 * cambia uno, se cambian los dos.
 */

/** (relleno claro, tinta fuerte). Ocho colores separados ΔE76 ≥ 11.3 entre sí. */
export const PALETA_COMBINADO: readonly { relleno: string; tinta: string }[] = [
  { relleno: "#E4D7FF", tinta: "#6D28D9" }, // lavanda
  { relleno: "#C9EEF4", tinta: "#155E75" }, // cian
  { relleno: "#FBD3E6", tinta: "#9D174D" }, // rosa
  { relleno: "#D8EFC0", tinta: "#3F6212" }, // lima
  { relleno: "#D2E0FC", tinta: "#1D4ED8" }, // azul
  { relleno: "#FDE2C2", tinta: "#9A3412" }, // naranja
  { relleno: "#FFF2A8", tinta: "#854D0E" }, // amarillo
  { relleno: "#CDEBD9", tinta: "#065F46" }, // menta
];

/** La guía para comparar: sin espacios ni mayúsculas (se dictan con espacios). */
export const normGuia = (guia: string | null | undefined): string =>
  (guia ?? "").replace(/\s+/g, "").toLowerCase();

/** `canal|guía normalizada`: la identidad de un envío combinado. */
export const claveGuia = (canal: string, guia: string | null | undefined): string =>
  `${canal}|${normGuia(guia)}`;

/** "…2532": los últimos 4 caracteres de la guía. */
export function codigoGuia(guia: string | null | undefined): string {
  const g = normGuia(guia);
  return g ? `…${g.slice(-4).toUpperCase()}` : "";
}

/** El color que le toca a una guía por sí misma (FNV-1a 32 bits sobre UTF-8). */
export function indicePreferido(clave: string): number {
  const bytes = new TextEncoder().encode(clave);
  let h = 0x811c9dc5;
  for (let i = 0; i < bytes.length; i++) {
    h = Math.imul(h ^ bytes[i], 0x01000193) >>> 0;
  }
  return h % PALETA_COMBINADO.length;
}

/**
 * Un índice de paleta por grupo, en el orden dado (el más viejo primero): su
 * color preferido, o el siguiente libre entre los grupos que comparten algún
 * DÍA con él. Gemela de `asignar_colores` en guias_del_dia.py — el día manda
 * porque el Excel se arma por día y un combinado entre el 12 y el 13 sale en
 * los dos. `grupos` es [clave, días AAAA-MM-DD].
 */
export function asignarColores(grupos: [string, string[]][]): number[] {
  const n = PALETA_COMBINADO.length;
  const porDia = new Map<string, Set<number>>();
  return grupos.map(([clave, dias]) => {
    const pref = indicePreferido(clave);
    const tomados = new Set<number>();
    for (const d of dias) for (const c of porDia.get(d) ?? []) tomados.add(c);
    let idx = pref;
    for (let k = 0; k < n; k++) {
      const cand = (pref + k) % n;
      if (!tomados.has(cand)) { idx = cand; break; }
    }
    for (const d of dias) porDia.set(d, (porDia.get(d) ?? new Set<number>()).add(idx));
    return idx;
  });
}

const FORMATO_DIA_MX = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Mexico_City", year: "numeric", month: "2-digit", day: "2-digit",
});

/** "AAAA-MM-DD" de un instante, en hora de la Ciudad de México (la del almacén). */
export function diaMX(ms: number): string {
  const partes = FORMATO_DIA_MX.formatToParts(new Date(ms));
  const v = (tipo: string) => partes.find((p) => p.type === tipo)?.value ?? "";
  return `${v("year")}-${v("month")}-${v("day")}`;
}

/** Acciones de la bitácora con las que la caja NO sale (no forman envío combinado).
 *
 *  `cancelada_sin_orden` es de la creación diferida (23-sep): el canal canceló
 *  mientras la venta esperaba su guía, así que nunca hubo orden ni la habrá.
 *  Si se colara, una venta muerta arrastraría a una viva al mismo grupo y el
 *  almacén buscaría en la caja algo que no existe.
 *
 *  `espera_guia` NO está aquí a propósito: esa venta SÍ va a salir, y si el
 *  canal ya le puso la misma guía que a otra, es la misma caja — verlo desde
 *  antes de que nazca su orden es justo lo útil. */
export const ACCIONES_CANCELADA: ReadonlySet<string> = new Set([
  "cancelada", "ya_cancelada", "no_se_pudo_cancelar", "solo_registro_cancelar", "nacio_cancelada",
  "cancelada_sin_orden",
]);

/** Lo mínimo de una fila de la bitácora para agruparla. */
export interface OrdenCombinable {
  canal: string;
  external_order_id: string;
  odoo_name: string | null;
  guia: string | null;
  accion: string;
  estado: string | null;
  /** Cuándo se GENERÓ la orden (la fecha con la que el Excel corta el día). */
  creado_at: string;
  lineas: { cantidad: number }[];
}

export interface Combinado {
  /** `canal|guía normalizada`. */
  clave: string;
  guia: string;
  /** "…2532". */
  codigo: string;
  n: number;
  /** Tinta fuerte: borde y letra. */
  color: string;
  /** Relleno claro: el mismo de la fila en el Excel. */
  suave: string;
  companeras: { odoo_name: string | null; external_order_id: string }[];
  /** Piezas de TODA la caja, no sólo de esta orden. */
  piezas: number;
}

export const claveOrden = (o: { canal: string; external_order_id: string }) =>
  `${o.canal}-${o.external_order_id}`;

/**
 * Agrupa por guía sobre la lista COMPLETA de UN canal —no la filtrada— para que
 * una orden sepa de su compañera aunque el buscador o el filtro la escondan.
 * Las canceladas no cuentan: no van en la caja.
 *
 * Se llama POR CANAL, como el Excel de un canal. (El Excel de "Ambos" reparte
 * los colores entre los dos canales a la vez; si algún día coincidieran un
 * combinado de Temu y uno de TikTok que piden el mismo color el mismo día, ese
 * archivo pintaría uno distinto. El código "…2532" sigue siendo el mismo.)
 */
export function combinadosDe<T extends OrdenCombinable>(lista: T[]): Record<string, Combinado> {
  const grupos = new Map<string, T[]>();
  for (const o of lista) {
    if (!normGuia(o.guia) || ACCIONES_CANCELADA.has(o.accion) || o.estado === "cancel") continue;
    const k = claveGuia(o.canal, o.guia);
    const arr = grupos.get(k) ?? [];
    // Distinta VENTA: el surtido dividido (una venta, dos órdenes) es otra cosa.
    if (!arr.some((x) => x.external_order_id === o.external_order_id)) arr.push(o);
    grupos.set(k, arr);
  }
  const t = (o: T) => Date.parse(o.creado_at) || 0;
  const multiples = [...grupos.entries()]
    .filter(([, g]) => g.length > 1)
    .map(([clave, g]) => ({ clave, g: [...g].sort((a, b) => t(a) - t(b)), desde: Math.min(...g.map(t)) }))
    .sort((a, b) => a.desde - b.desde || (a.clave < b.clave ? -1 : a.clave > b.clave ? 1 : 0));

  const indices = asignarColores(
    multiples.map((m) => [m.clave, [...new Set(m.g.map((o) => diaMX(t(o))))].sort()]));

  const out: Record<string, Combinado> = {};
  multiples.forEach(({ clave, g }, i) => {
    const pal = PALETA_COMBINADO[indices[i]];
    const piezas = g.reduce((n, o) => n + o.lineas.reduce((s, l) => s + (l.cantidad ?? 0), 0), 0);
    for (const o of g) {
      out[claveOrden(o)] = {
        clave,
        guia: o.guia ?? "",
        codigo: codigoGuia(o.guia),
        n: g.length,
        color: pal.tinta,
        suave: pal.relleno,
        companeras: g.filter((x) => x !== o)
          .map((x) => ({ odoo_name: x.odoo_name, external_order_id: x.external_order_id })),
        piezas,
      };
    }
  });
  return out;
}
