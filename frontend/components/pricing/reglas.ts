// reglas.ts — Las reglas de «Qué hacer» del Monitor de precios FULL.
//
// Port FIEL del artifact de José (14-oct-2026): funciones puras que reciben una
// publicación y los umbrales. Lo único nuevo son las reglas de SIN VENTAS
// (Brandon, 15-oct-2026), que van antes que las de José para las publicaciones
// que no vendieron una pieza en 30 días, de las dos cuentas de ML:
//
//   1. Descuento «super agresivo» (más de 60 %): SOLO 3 % más esta semana. Si
//      con eso vende, se mantiene; si no, se ajusta el precio para que muestre
//      30 % de descuento.
//   2. Sin descuento: 5 % si el ticket es menor a $300; 10 % si es de $300 o más.
//   3. Con descuento de 60 % o menos: 5 % más.
//
// Todos los números son umbrales editables («Reglas y umbrales» en la pantalla).
//
// Los arreglos de visitas y ventas van del día 0 (`meta.d0`) a H = hoy; H sale
// del largo del arreglo de visitas (119 en la foto del 14-oct).

import type { PricingComp, PricingPub } from "@/lib/api";

export interface Reglas {
  /** Ventas al día desde las que son «altas» (≈ 3 al día). */
  altas: number;
  /** Ventas al día desde las que son «bajas» (1–2 al día); abajo, «pocas». */
  bajas: number;
  /** Conversión de 30 días: arriba de esto y con ventas altas, subir. */
  conv: number;
  /** … solo si el stock alcanza para estos días o menos. */
  diasStock: number;
  /** ±% de la competencia = «al nivel». */
  banda: number;
  /** Cuánto subir cuando toca subir. */
  paso: number;
  /** La competencia de referencia: mediana o el más barato. */
  ref: "med" | "min";
  /** «Vende igual» = ventas después ≤ antes × (1 + tol); «bajó la conversión» = −tol. */
  tolHist: number;
  /** Piezas vendidas a precio alto, como mínimo, para juzgar que bajar no ayudó. */
  minHist: number;
  /** % de cambio en ventas por % de cambio en precio (31 pares de dos cuentas). */
  elasticidad: number;

  // ── SIN VENTAS (Brandon, 15-oct-2026) ──
  /** Aplicar las reglas de sin ventas (apagado = el monitor como lo dejó José). */
  sinVentas: boolean;
  /** Descuento «super agresivo»: más de esto. */
  svAgresivo: number;
  /** Lo único que se agrega esta semana si ya es agresivo. */
  svExtraAgresivo: number;
  /** Si con eso no vende en la semana: el descuento que debe mostrar. */
  svMostrar: number;
  /** Cómo se llega a mostrar ese descuento. */
  svLuego: "normal" | "precio";
  /** Lo que se agrega si ya tiene descuento (agresivo o menos). */
  svExtra: number;
  /** Ticket: abajo de esto es «bajo». */
  svCorte: number;
  /** Sin descuento y ticket bajo. */
  svBajo: number;
  /** Sin descuento y ticket de svCorte o más. */
  svAlto: number;
  /** Cómo se suma el extra: puntos sobre el precio normal (62 → 65 %) o % sobre el precio de hoy. */
  svModo: "puntos" | "precio";
}

export const REGLAS: Reglas = {
  altas: 2.5,
  bajas: 1,
  conv: 0.05,
  diasStock: 9,
  banda: 0.03,
  paso: 0.05,
  ref: "med",
  tolHist: 0.1,
  minHist: 3,
  elasticidad: -2,
  sinVentas: true,
  svAgresivo: 0.6,
  svExtraAgresivo: 0.03,
  svMostrar: 0.3,
  svLuego: "normal",
  svExtra: 0.05,
  svCorte: 300,
  svBajo: 0.05,
  svAlto: 0.1,
  svModo: "puntos",
};

export const hoyDe = (p: PricingPub): number => p.vis.length - 1;

const suma = (a: number[], i0: number, i1: number, H: number): number => {
  let s = 0;
  for (let i = Math.max(0, i0); i <= Math.min(H, i1); i++) s += a[i] || 0;
  return s;
};
const pagadas = (p: PricingPub) => p.o.filter((o) => !o[3]);
const unidades = (p: PricingPub, i0: number, i1: number): number =>
  pagadas(p).reduce((s, o) => s + (o[0] >= i0 && o[0] <= i1 ? o[2] : 0), 0);

/** [[precio, piezas], …] → el precio donde se acumula la mitad de las piezas. */
function medianaPonderada(pares: [number, number][]): number | null {
  const xs = pares.slice().sort((a, b) => a[0] - b[0]);
  const tot = xs.reduce((s, x) => s + x[1], 0);
  let acc = 0;
  for (const x of xs) {
    acc += x[1];
    if (acc >= tot / 2) return x[0];
  }
  return null;
}

export interface Historia {
  pA: number | null;
  dHi: number;
  aIni: number;
  bIni: number;
  lenA: number;
  lenB: number;
  uA: number;
  uB: number;
  vA: number;
  vB: number;
  cA: number | null;
  cB: number | null;
  ventasNo: boolean;
  convNo: boolean;
  dispara: boolean;
}

/** «Si vendía a un precio mayor y al bajarlo bajaron las ventas o la conversión, o vende igual: subir». */
export function historia(p: PricingPub, precio: number, R: Reglas): Historia | null {
  const H = hoyDe(p);
  const altos = pagadas(p).filter((o) => o[0] >= H - 89 && o[1] >= precio * 1.05);
  if (!altos.length) return null;
  const dHi = Math.max(...altos.map((o) => o[0])); // la última venta a precio alto
  const bIni = Math.max(dHi + 1, H - 29);
  const lenB = H - bIni + 1;
  if (H - dHi < 7) return null; // muy reciente para juzgar
  const primera = Math.min(
    ...[p.vis.findIndex((v) => v > 0), ...pagadas(p).map((o) => o[0])].filter((i) => i >= 0),
  );
  const aIni = Math.max(dHi - 29, primera);
  const lenA = dHi - aIni + 1;
  if (lenA < 7) return null;
  const enA = pagadas(p).filter((o) => o[0] >= aIni && o[0] <= dHi);
  const uA = enA.reduce((s, o) => s + o[2], 0);
  const uAltas = enA.filter((o) => o[1] >= precio * 1.05).reduce((s, o) => s + o[2], 0);
  if (!uA || uAltas / uA < 0.6 || uAltas < R.minHist) return null; // antes no vendía sobre todo a precio alto
  const uB = unidades(p, bIni, H);
  const visA = suma(p.vis, aIni, dHi, H);
  const visB = suma(p.vis, bIni, H, H);
  const vA = uA / lenA;
  const vB = uB / lenB;
  const cA = visA ? uA / visA : null;
  const cB = visB ? uB / visB : null;
  const pA = medianaPonderada(enA.filter((o) => o[1] >= precio * 1.05).map((o) => [o[1], o[2]] as [number, number]));
  const ventasNo = vB <= vA * (1 + R.tolHist);
  const convNo = cA != null && cB != null && cB < cA * (1 - R.tolHist);
  return { pA, dHi, aIni, bIni, lenA, lenB, uA, uB, vA, vB, cA, cB, ventasNo, convNo, dispara: ventasNo || convNo };
}

export type Posicion = "arriba" | "abajo" | "nivel";

export interface Metricas {
  precio: number;
  normal: number | null;
  /** Descuento sobre el precio normal (0 = sin descuento). */
  desc: number;
  u7: number;
  u14: number;
  u30: number;
  u60: number;
  vis30: number;
  /** Piezas al día: 14 días si hubo 3 o más; si no, 30. */
  vel: number;
  conv: number | null;
  /** Días de stock al ritmo de hoy (Infinity sin ventas). */
  dias: number;
  ref: number | null;
  dif: number | null;
  pos: Posicion | null;
  hist: Historia | null;
}

export function metricas(p: PricingPub, R: Reglas): Metricas {
  const H = hoyDe(p);
  const precio = p.p.a ?? 0;
  const normal = p.p.n;
  const u7 = unidades(p, H - 6, H);
  const u14 = unidades(p, H - 13, H);
  const u30 = unidades(p, H - 29, H);
  const u60 = unidades(p, H - 59, H);
  const vis30 = suma(p.vis, H - 29, H, H);
  const vel = u14 >= 3 ? u14 / 14 : u30 / 30;
  const c: PricingComp = p.comp || {};
  const ref = c.ok ? (R.ref === "min" && c.min ? c.min : c.med ?? null) : null;
  const dif = ref && precio ? precio / ref - 1 : null;
  return {
    precio,
    normal,
    desc: normal && precio && normal > precio ? 1 - precio / normal : 0,
    u7,
    u14,
    u30,
    u60,
    vis30,
    vel,
    conv: vis30 ? u30 / vis30 : null,
    dias: vel > 0 ? p.stock / vel : Infinity,
    ref,
    dif,
    pos: dif == null ? null : dif > R.banda ? "arriba" : dif < -R.banda ? "abajo" : "nivel",
    hist: precio ? historia(p, precio, R) : null,
  };
}

export type ClaveAccion =
  | "sv_agresivo"
  | "sv_extra"
  | "sv_nuevo"
  | "subir_hist"
  | "subir"
  | "subir_bajo"
  | "min_comp"
  | "bajar_nivel"
  | "nomover"
  | "mantener"
  | "noref"
  | "sinstock";

export type Sentido = "up" | "down" | "hold" | "none";

export const SENTIDO: Record<ClaveAccion, Sentido> = {
  sv_agresivo: "down",
  sv_extra: "down",
  sv_nuevo: "down",
  subir_hist: "up",
  subir: "up",
  subir_bajo: "up",
  min_comp: "down",
  bajar_nivel: "down",
  nomover: "hold",
  mantener: "hold",
  noref: "none",
  sinstock: "none",
};

const pctCorto = (x: number) => `${+(x * 100).toFixed(1)} %`;

/** El nombre de cada acción. Las de sin ventas dicen sus números (cambian con los umbrales). */
export function etiqueta(k: ClaveAccion, R: Reglas): string {
  switch (k) {
    case "sv_agresivo":
      return `Sin ventas: +${pctCorto(R.svExtraAgresivo)} (ya > ${pctCorto(R.svAgresivo)})`;
    case "sv_extra":
      return `Sin ventas: +${pctCorto(R.svExtra)} de descuento`;
    case "sv_nuevo":
      return `Sin ventas: descuento ${pctCorto(R.svBajo)} / ${pctCorto(R.svAlto)}`;
    case "subir_hist":
      return "Subir: bajar no ayudó";
    case "subir":
      return "Subir precio";
    case "subir_bajo":
      return "Subir a competencia − $1";
    case "min_comp":
      return "Al mínimo de competencia";
    case "bajar_nivel":
      return "Descuento al nivel de competencia";
    case "nomover":
      return "No mover";
    case "mantener":
      return "Mantener";
    case "noref":
      return "Sin referencia";
    case "sinstock":
      return "Sin stock";
  }
}

/** El orden de «Por acción» y de las tarjetas de arriba. Las de sin ventas, primero: son el trabajo de la semana. */
export const ORDEN_ACC: ClaveAccion[] = [
  "sv_agresivo", "sv_extra", "sv_nuevo",
  "subir_hist", "subir", "subir_bajo", "min_comp", "bajar_nivel", "nomover", "mantener", "noref", "sinstock",
];

export interface Accion {
  k: ClaveAccion;
  /** El precio que se propone (lo que pagaría el comprador). */
  precio?: number | null;
  por: string;
  /** Rótulo de la fila cuando dice más que el de la acción (p. ej. «descuento 10 %»). */
  t?: string;
  /** Sin ventas: el descuento que quedaría mostrado con el precio propuesto. */
  descNuevo?: number;
  /** Sin ventas, agresivo: lo que sigue si en 7 días no vende. */
  luego?: { precio: number; normal: number; desc: number };
}

const pct = (x: number) => (x * 100).toFixed(1).replace(".0", "") + " %";
const pesos = (x: number) => "$" + Math.round(x).toLocaleString("es-MX");
// Pesos enteros, sin quedarse cortos: el precio propuesto se redondea hacia
// ABAJO y el precio normal re-basado hacia ARRIBA, para que el descuento que se
// ve nunca sea menor al de la regla ($188 × 0.95 = $178.6 → $178, 5.3 %; con
// redondeo normal saldría $179, 4.8 %, y ML pide al menos 5 % para mostrarlo).
// El 1e-6 absorbe el ruido de punto flotante (349.9999999 sigue siendo 350).
const abajo = (x: number) => Math.floor(x + 1e-6);
const arriba = (x: number) => Math.ceil(x - 1e-6);

/**
 * SIN VENTAS (Brandon, 15-oct-2026). Cero piezas pagadas en 30 días, con stock.
 * No depende de la competencia: aplica aunque no haya referencia.
 */
export function accionSinVentas(m: Metricas, R: Reglas): Accion {
  const normal = m.normal && m.normal > 0 ? m.normal : m.precio;
  // El extra: en «puntos» se suma al % de descuento sobre el precio normal
  // (62 % → 65 %); en «precio», es un % menos sobre lo que se cobra hoy.
  const conExtra = (extra: number) =>
    abajo(R.svModo === "precio" ? m.precio * (1 - extra) : normal * (1 - m.desc - extra));
  const descDe = (precio: number) => (normal > 0 ? 1 - precio / normal : 0);
  const cuanto = (extra: number) => (R.svModo === "precio" ? `${pct(extra)} menos sobre el precio de hoy` : `${pct(extra)} más de descuento`);

  if (m.desc > R.svAgresivo) {
    const precio = conExtra(R.svExtraAgresivo);
    const desc = descDe(precio);
    // Lo que sigue si en la semana no vende. «normal»: el MISMO precio, con el
    // precio normal re-basado para que el descuento se vea de svMostrar.
    // «precio»: cobrar el precio normal de hoy menos svMostrar.
    const luego = R.svLuego === "normal"
      ? { precio, normal: arriba(precio / (1 - R.svMostrar)), desc: R.svMostrar }
      : { precio: abajo(normal * (1 - R.svMostrar)), normal, desc: R.svMostrar };
    const siguiente = R.svLuego === "normal"
      ? `bajar el precio normal a ${pesos(luego.normal)} para que el mismo ${pesos(precio)} muestre ${pct(R.svMostrar)} de descuento`
      : `cobrar ${pesos(luego.precio)} (precio normal ${pesos(normal)} con ${pct(R.svMostrar)} de descuento)`;
    return {
      k: "sv_agresivo", precio, descNuevo: desc, luego,
      por: `Sin ventas en 30 días y ya con un descuento super agresivo (${pct(m.desc)}, más de ${pct(R.svAgresivo)}): ` +
        `solo ${cuanto(R.svExtraAgresivo)} esta semana, a ${pesos(precio)} (${pct(desc)} sobre ${pesos(normal)}). ` +
        `Si en 7 días vende, mantener; si no, ${siguiente}.`,
    };
  }
  if (m.desc <= 0.004) {
    const bajo = m.precio < R.svCorte;
    const d = bajo ? R.svBajo : R.svAlto;
    const precio = abajo(m.precio * (1 - d));
    return {
      k: "sv_nuevo", precio, descNuevo: descDe(precio), t: `Sin ventas: descuento ${pctCorto(d)}`,
      por: `Sin ventas en 30 días y sin descuento. Ticket ${bajo ? "menor a" : "de"} ${pesos(R.svCorte)}${bajo ? "" : " o más"}: ` +
        `${pct(d)} de descuento, de ${pesos(m.precio)} a ${pesos(precio)}.`,
    };
  }
  const precio = conExtra(R.svExtra);
  const desc = descDe(precio);
  return {
    k: "sv_extra", precio, descNuevo: desc,
    por: `Sin ventas en 30 días con ${pct(m.desc)} de descuento (${pct(R.svAgresivo)} o menos): ` +
      `${cuanto(R.svExtra)}, a ${pesos(precio)} (${pct(desc)} sobre ${pesos(normal)}).`,
  };
}

export function accion(p: PricingPub, m: Metricas, R: Reglas): Accion {
  const c: PricingComp = p.comp || {};
  const H = hoyDe(p);
  if (!(p.stock > 0)) return { k: "sinstock", por: "No hay piezas disponibles en FULL." };
  if (R.sinVentas && m.u30 === 0 && m.precio > 0) return accionSinVentas(m, R);
  if (m.hist && m.hist.dispara) {
    const h = m.hist;
    return {
      k: "subir_hist",
      precio: h.pA,
      por:
        `Vendía a ${pesos(h.pA ?? 0)} hasta hace ${H - h.dHi} días. Después del cambio vende ` +
        `${h.vB.toFixed(2)} al día (antes ${h.vA.toFixed(2)})` +
        (h.cA != null && h.cB != null ? ` y convierte ${pct(h.cB)} (antes ${pct(h.cA)})` : "") +
        ".",
    };
  }
  if (!m.ref) return { k: "noref", por: c.motivo || "Sin precio de competencia fiel." };
  if (m.vel >= R.altas) {
    if (m.conv != null && m.conv > R.conv && m.dias <= R.diasStock)
      return {
        k: "subir",
        precio: m.precio * (1 + R.paso),
        por: `Vende ${m.vel.toFixed(1)} al día con conversión de ${pct(m.conv)} y el stock alcanza para ${Math.round(m.dias)} días.`,
      };
    if (m.conv == null || m.conv <= R.conv)
      return { k: "nomover", por: `Vende ${m.vel.toFixed(1)} al día con conversión de ${m.conv == null ? "—" : pct(m.conv)}: no mover.` };
    return {
      k: "mantener",
      por: `Vende ${m.vel.toFixed(1)} al día y convierte ${pct(m.conv)}, pero el stock alcanza para ${Math.round(m.dias)} días (más de ${R.diasStock}).`,
    };
  }
  let obj: number;
  let k: ClaveAccion;
  let por: string;
  if (m.vel >= R.bajas) {
    obj = (c.min || c.med) ?? 0;
    k = "min_comp";
    por = `Ventas bajas (${m.vel.toFixed(1)} al día): al precio del rival más barato` + (c.min ? "." : " (sin mínimo medido: la mediana).");
  } else if (m.pos === "arriba") {
    obj = m.ref;
    k = "bajar_nivel";
    por = `Pocas ventas (${m.u30} en 30 días) y está ${pct(m.dif ?? 0)} arriba de la competencia.`;
  } else if (m.pos === "abajo") {
    obj = m.ref - 1;
    k = "subir_bajo";
    por = `Está ${pct(-(m.dif ?? 0))} abajo de la competencia.`;
  } else {
    return { k: "mantener", por: `Al nivel de la competencia (${(m.dif ?? 0) >= 0 ? "+" : ""}${pct(m.dif ?? 0)}).` };
  }
  if (Math.abs(obj / m.precio - 1) < 0.01) return { k: "mantener", por: por + " Ya está en ese precio." };
  return { k, precio: obj, por };
}

export interface Proyeccion {
  hoy: number;
  sug: number | null;
  estimado: boolean;
  pHoy: number;
  pSug: number | null;
}

// Venta de los próximos 7 días, al precio de hoy y al sugerido. Con ventas: el ritmo de hoy × 7, y al sugerido ese
// ritmo × (precio sugerido / hoy)^elasticidad. Sin ventas: hoy 0 (es lo que pasa a ese precio); al sugerido, las
// visitas al día × 7 × la conversión típica al precio de mercado × (sugerido / mercado)^elasticidad (estimado).
// Nunca más que el stock.
export function proyeccion7(p: PricingPub, m: Metricas, a: Accion, R: Reglas, convTipica: number | null): Proyeccion {
  const H = hoyDe(p);
  const tope = (x: number) => Math.max(0, Math.min(p.stock || 0, x));
  const hoy = tope(m.vel * 7);
  const obj = a.precio || null;
  if (!obj) return { hoy, sug: null, estimado: false, pHoy: m.precio, pSug: null };
  let sug: number;
  let estimado = false;
  if (m.vel > 0) sug = m.vel * 7 * Math.pow(obj / m.precio, R.elasticidad);
  else {
    const visDia = suma(p.vis, H - 13, H, H) / 14;
    sug = m.ref && convTipica ? visDia * 7 * convTipica * Math.pow(obj / m.ref, R.elasticidad) : 0;
    estimado = true;
  }
  return { hoy, sug: tope(sug), estimado, pHoy: m.precio, pSug: obj };
}

/** Descuento que hace falta sobre el precio normal para llegar al precio objetivo (o subir el normal). */
export function descuentoPara(normal: number | null, objetivo: number | null | undefined) {
  if (!objetivo || !normal) return null;
  return objetivo <= normal ? { desc: 1 - objetivo / normal, subirNormal: false } : { desc: 0, subirNormal: true };
}

export interface Fila {
  i: number;
  p: PricingPub;
  m: Metricas;
  a: Accion;
  x: Proyeccion;
}

/** Todas las filas y la conversión típica al precio de mercado. */
export function calcular(pubs: PricingPub[], R: Reglas): { filas: Fila[]; convTipica: number | null } {
  const base = pubs.map((p, i) => {
    const m = metricas(p, R);
    return { i, p, m, a: accion(p, m, R) };
  });
  // Conversión típica al precio de mercado: mediana de las nuestras con 3+ ventas en 30 días, al nivel o abajo de la competencia.
  const cs = base
    .filter((f) => f.m.u30 >= 3 && f.m.conv != null && (f.m.pos === "nivel" || f.m.pos === "abajo"))
    .map((f) => f.m.conv as number)
    .sort((a, b) => a - b);
  const convTipica = cs.length ? cs[Math.floor(cs.length / 2)] : null;
  return { filas: base.map((f) => ({ ...f, x: proyeccion7(f.p, f.m, f.a, R, convTipica) })), convTipica };
}
