// logica.ts — Las cuentas del CATÁLOGO (v0.703.0), portadas tal cual del artifact del catálogo vivo
// (`CATALOGO_VIVO/scripts/plantilla_pl.html`, rama `conocimiento`): qué precio usa cada fila, cuánto vale lo que
// queda, los filtros, los totales y la copia para Excel. Si el artifact cambia una cuenta, se cambia aquí igual:
// la pestaña y el artifact deben dar las MISMAS cifras.
//
// Lo único que no está: el costo (el repo es público, ver `backend/services/catalogo.py`).

// ── Los datos (forma de `backend/data/catalogo/catalogo.json.gz`) ────────────

/** Precio de un canal. ML con `o: "e"` viene del archivo de Eduardo / v3; lo demás, de la competencia medida. */
export interface Precio {
  o?: string; f?: string; cl?: string; m: number; me?: number; lo?: number; hi?: number; d?: number; a?: number;
  b?: number; n?: number; x?: number; dz?: number; pk?: number; q?: string; ej?: [string, number, string][];
  nv?: string; cg?: string; pn?: number; dp?: number; tp?: number; sr?: number; rq?: string; vd?: number;
  ue?: number; pa?: number; ni?: number; sg?: number; rz?: number; aj?: number; v3?: V3;
}

export interface V3 {
  fu?: string; fs?: Record<string, number>; cc?: number; an?: number; m?: (string | number | null)[];
  mq?: string[]; nt?: string[]; rv?: number;
}

/** Estimado: [precio, base, múltiplo, nivel, topado]. Por costo (`c`) el múltiplo no viaja. */
export type Estimado = [number, string, number | null, string | null, number];

export interface Renglon { c: string | number; av?: number; fv?: number | string; ao?: number; fo?: number[]; m?: string }

export interface Fila {
  s: string; t: string; n?: string; tf?: string; td?: number; tg?: string; tv?: string; trv?: number; trm?: string;
  tfm?: string; k?: number; kq?: string; f?: number; fo?: "pl" | "odoo";
  pc?: number; pq?: number; pp?: number; ps?: number; pz?: number; uf?: number; cl?: Record<string, number>;
  so?: number; sin?: number; nr?: number; dm?: number; rm?: number; pe?: number; ec?: string | number; lb?: number;
  en?: number; aj?: number; v?: number; v1?: string; v2?: string; nv?: number; mr?: { lo: number; me: number; hi: number; rq?: string };
  ct?: Record<string, number>; cm?: string[]; rn?: Renglon[]; ml?: Precio; az?: Precio; em?: Estimado; ea?: Estimado;
  v3?: V3;
  // Calculado al cargar (`preparar`).
  _i: number; _q: number | null; _fm: boolean; _cm: string | null; _fa: boolean; _pm: number | null; _pa: number | null;
  _dp: number | null; _vm: number | null; _va: number | null; _em: number | null; _ea: number | null;
  _tm: number | null; _ta: number | null; _m: string | null; _b: string; _parte?: number;
  /** Por qué hay que validarlo (llaves de MOTIVOS); vacío = nada que validar. */
  _mv: string[];
  /** Publicado HOY en Mercado Libre (v0.708.0): [cuenta, active|paused]; [] = no; undefined = todavía sin dato. */
  _pub?: [string, string][];
}

/** `GET /api/catalogo/publicados`: lo publicado HOY en ML (activa o pausada), por cuenta, y la hora del último sync. */
export interface CatalogoPublicados { actualizado: string | null; ml: Record<string, [string, string][]> }

export interface Contenedor {
  n: number | null; clave: string; codigos: string[]; orig: number[]; val: number[]; pz: number; pz_orig: number;
  pz_val: number; skus: number; sin: number; sal: number;
}

export interface Catalogo {
  meta: {
    generado: string; preparado: string;
    fotos: { lado: number; cols: number; por_hoja: number; total: number; hojas: number; pl_original: number;
      pl_artifact: number; odoo: number; sin: number };
  };
  fuentes: Record<string, string>;
  resumen: Record<string, any>;
  rot: Record<string, string>;
  estimacion: { tc?: number; az?: { razon_ml?: { p?: number; b?: number } }; az_descartados?: number };
  K: string[][];
  A: Record<string, string>;
  C: Contenedor[];
  mercado: { ml?: { produccion?: number; produccion_leido?: string; edu?: any }; amazon?: { actualizado?: string; palabras?: number; grupos?: number; juzgados?: number } };
  v3?: { version?: string; con_precio?: number; a_mano?: number; revisar?: number; estimado_costo?: number; sin_precio?: number };
  R: Fila[];
}

// ── Formatos ─────────────────────────────────────────────────────────────────

const N0 = new Intl.NumberFormat("es-MX", { maximumFractionDigits: 0 });
const F0 = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 0 });
const F2 = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const nf = (n: number | null | undefined) => (n == null ? "—" : N0.format(n));
export const fm0 = (n: number | null | undefined) => (n == null ? "—" : F0.format(n));
export const fm = (n: number | null | undefined) => (n == null ? "—" : F2.format(n));
export const mill = (n: number | null | undefined) =>
  n == null ? "—" : Math.abs(n) >= 1e6 ? `$${(n / 1e6).toFixed(1)} M` : fm0(n);
export function hora(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleString("es-MX", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

// ── Rótulos ──────────────────────────────────────────────────────────────────

export const METODO: Record<string, string> = {
  foto: "misma foto en el original", texto: "mismo nombre y cantidad", bodega: "bodega lo anotó en el validado",
  foto_odoo: "la foto de Odoo es la del renglón", foto_parecida: "foto de Odoo casi idéntica a la del renglón",
  cantidad: "lo recibido en la compra coincide con el renglón", ia: "inferido por IA con el nombre, sin foto",
};
export const CORTO: Record<string, string> = {
  full: "Full", fba: "Amazon", wfs: "Walmart", parolera: "Parolera", canales: "canales", directa: "directa", proveedor: "devuelto",
};
export const FML: Record<string, string> = {
  f: "media del mismo producto", n: "nuestro precio en ML", r: "revisado a mano", b: "media de su categoría",
  k: "mercado sin marcas (v3)", p: "medido por Competencia del panel",
};
export const FML_L: Record<string, string> = {
  f: "Exacto: media de las publicaciones del mismo producto",
  n: "Nuestro precio publicado en Mercado Libre (no hubo publicaciones que promediar)",
  r: "Revisado a mano (las líneas de mayor valor)",
  b: "Banda: media de los más vendidos de su categoría, que no son el mismo producto",
  k: "Mercado sin marcas de renombre (v3): el ranking de más vendidos de Mercado Libre, o la mediana de búsqueda y banda con la categoría corregida",
  p: "Medido antes por Competencia del panel (lo que su archivo no trae)",
};
export const ORIGEN: Record<string, string> = {
  p: "rivales del mismo producto (Competencia del panel)", g: "rivales de otra variante del mismo modelo",
  c: "catálogo de Mercado Libre", k: "búsqueda por palabra clave",
};
export const FV3: Record<string, string> = {
  bm: "ranking de más vendidos de Mercado Libre", bv: "banda de su categoría, sin marcas",
  ka: "búsqueda en Amazon ÷ 1.443", km: "búsqueda en Mercado Libre",
};
export const REV3: Record<string, string> = {
  banda: "banda", busqueda_ml: "búsqueda en Mercado Libre", busqueda_amazon: "búsqueda en Amazon",
  calculo_paquete: "cálculo de paquete a pieza", estimado_costo: "estimado por costo",
  precio_v3_esta_bien: "el precio v3 está bien", sin_precio: "sin precio",
};
export const NIVEL: Record<string, string> = {
  h: "su tipo de producto", s: "su subcategoría", r: "su categoría", g: "todo el inventario",
};

export const v3de = (r: Fila): V3 | null => (r.ml && r.ml.v3) || r.v3 || null;
export const fuenteV3 = (fu?: string) =>
  String(fu || "").replace(/\b(bm|bv|ka|km)\b/g, (k) => FV3[k]).replace(/(\S)\(/g, "$1 (").replace(/\+/g, " + ");

export function comoSeEstimo(e: Estimado | undefined | null): string {
  if (!e) return "";
  const tope = e[4] ? "; topado al precio del 90% más caro de lo medido en su subcategoría" : "";
  const nivel = NIVEL[e[3] || ""] || "su subcategoría";
  if (e[1] === "m") return `su precio de Mercado Libre × ${e[2]} (lo que Amazon cobra de más por lo mismo en ${nivel})${tope}`;
  if (e[1] === "c") return `su costo × lo que se vende contra su costo en ${nivel}${tope}`;
  if (e[1] === "i") return `su propio indicio (una sola publicación, otro tamaño de paquete o catálogo)${tope}`;
  if (e[1] === "t") return `el precio de ese tipo de producto${tope}`;
  return `el precio promedio por pieza de ${nivel}${tope}`;
}

/** Lo que dice la celda de un estimado («precio de ML × 1.78», «costo», …). */
export function etiquetaEstimado(e: Estimado): string {
  return e[1] === "m" ? `precio de ML × ${e[2]}` : e[1] === "c" ? "por costo" : e[1] === "i" ? "su indicio"
    : e[1] === "t" ? "tipo de producto" : `promedio de ${NIVEL[e[3] || ""] || "su subcategoría"}`;
}

// ── Categorías ───────────────────────────────────────────────────────────────

export const c1 = (D: Catalogo, r: Fila) => (r.k == null ? "Sin categoría" : D.K[r.k][0]);
export const c2 = (D: Catalogo, r: Fila) => (r.k == null ? "" : D.K[r.k][1] || "");

// ── Preparar (una vez al cargar) ─────────────────────────────────────────────

/** Los campos calculados de cada fila: qué precio usa, cuánto vale lo que queda, cómo se empató. */
export function preparar(R: Fila[]): void {
  R.forEach((r, i) => {
    r._i = i;
    r._q = r.pc != null ? (r.pq ?? 0) : null; // piezas que quedan (solo si hay packing list)
    // MEDIDO = al menos dos publicaciones del mismo producto y paquete. ML del archivo de Eduardo (`o: "e"`) manda.
    const edu = !!(r.ml && r.ml.o === "e");
    r._fm = edu || !!(!r.sin && r.ml && (r.ml.n ?? 0) >= 2 && !r.ml.x && r.ml.o !== "c");
    r._cm = r._fm ? (edu ? r.ml!.cl ?? null : "p") : null;
    r._fa = !!(!r.sin && r.az && (r.az.n ?? 0) >= 2 && !r.az.x && !r.az.dz);
    r._pm = r._fm ? r.ml!.m : r.em ? r.em[0] : null;
    r._pa = r._fa ? r.az!.m : r.ea ? r.ea[0] : null;
    r._dp = edu ? r.ml!.dp ?? null : r._pm;
    r._vm = r._q != null && r._fm ? r._q * r.ml!.m : null;
    r._va = r._q != null && r._fa ? r._q * r.az!.m : null;
    r._em = r._q != null && !r._fm && r.em ? r._q * r.em[0] : null;
    r._ea = r._q != null && !r._fa && r.ea ? r._q * r.ea[0] : null;
    r._tm = r._vm != null ? r._vm : r._em;
    r._ta = r._va != null ? r._va : r._ea;
    r._m = r.cm ? ["foto", "texto", "foto_odoo", "foto_parecida", "cantidad", "ia", "bodega"].filter((k) => r.cm!.indexOf(k) >= 0)[0] ?? null : null;
    r._b = `${r.s} ${r.t} ${r.n || ""} ${Object.keys(r.ct || {}).join(" ")}`.toLowerCase();
    r._mv = motivos(r);
  });
}

/** Le pone a cada fila lo publicado en ML (v0.708.0). Un producto sin SKU no puede estar publicado. */
export function marcarPublicados(R: Fila[], ml: CatalogoPublicados["ml"]): void {
  R.forEach((r) => { r._pub = r.sin ? [] : ml[String(r.s || "").toUpperCase()] || []; });
}

export const publicadoEnML = (r: Fila) => !!(r._pub && r._pub.length);

/** «SANCORFASHION» no cabe en la columna. */
export const cuentaCorta = (c: string) => (c === "SANCORFASHION" ? "SANCOR" : c);

/** La parte de una fila que llegó en UN contenedor: sus piezas de ahí, y lo que salió y queda en esa proporción. */
export function enContenedor(r: Fila, c: string): Fila {
  const pz = r.ct ? r.ct[c] : null;
  if (pz == null || !r.pc || pz >= r.pc) return r;
  const f = pz / r.pc;
  const x: Fila = { ...r };
  x._parte = r.pc;
  x.pc = pz;
  x.pq = Math.round((r.pq ?? 0) * f);
  delete x.pp;
  if (r.ps != null) x.ps = Math.round(r.ps * f);
  if (r.pz != null) x.pz = Math.round(r.pz * f);
  x._q = x.pq;
  x._vm = r._vm != null ? x._q * (r._pm ?? 0) : null;
  x._em = r._em != null ? x._q * (r._pm ?? 0) : null;
  x._va = r._va != null ? x._q * (r._pa ?? 0) : null;
  x._ea = r._ea != null ? x._q * (r._pa ?? 0) : null;
  x._tm = x._vm != null ? x._vm : x._em;
  x._ta = x._va != null ? x._va : x._ea;
  return x;
}

// ── Filtros ──────────────────────────────────────────────────────────────────

/**
 * `val`: "" = todo el catálogo · "*" = lo que hay que validar, por cualquier motivo · una llave de MOTIVOS.
 * `sku`: "" · "con" · "sin" (tiene SKU o es un renglón del packing list sin SKU). `pub`: "" · "si" · "no" (publicado
 * HOY en Mercado Libre). Los dos de v0.708.0 (Brandon, 30-oct-2026).
 */
export interface Filtros {
  q: string; cat: string; sub: string; est: string; con: string; pre: string; emp: string; val: string; sku: string; pub: string;
}
export const SIN_FILTROS: Filtros = { q: "", cat: "", sub: "", est: "", con: "", pre: "", emp: "", val: "", sku: "", pub: "" };
/** Como abre la pestaña (Brandon, 30-oct-2026): «que muestre lo que necesitamos validar primero que nada». */
export const POR_VALIDAR: Filtros = { ...SIN_FILTROS, val: "*" };

// ── Por validar (v0.705.0) ───────────────────────────────────────────────────
//
// Lo que el propio catálogo marca como dudoso, en un producto que TODAVÍA TIENE MERCANCÍA y se vende (lo SOLD OUT y
// los insumos no se validan: no hay nada que vender). Cada motivo sale de un dato del artifact, no de una regla nueva.

export const MOTIVOS: [string, string, string][] = [
  ["precio", "Precio de ML por revisar", "El paquete de precios v3 lo dejó marcado: las fuentes difieren mucho, la única fuente es parcial o no hubo fuentes."],
  ["bodega", "Contenedor sin validar por bodega", "Sus piezas son las que declara el proveedor: bodega todavía no las contó."],
  ["titulo", "Título por confirmar", "La IA avisó que el título lo está suponiendo, o pidió que lo revise una persona."],
  ["foto", "La foto no corresponde", "La foto no corresponde al título, o la del catálogo y la del packing list son productos distintos."],
  ["sin_sku", "Sin SKU", "Renglones del packing list a los que nadie les ha puesto SKU."],
  ["empate", "Empate inferido por IA", "La IA emparejó el SKU con su renglón por el nombre y las piezas: confirmarlo al validar el contenedor."],
  ["sin_precio", "Sin precio de ML", "No tiene precio de Mercado Libre: ni medido ni estimado."],
  ["cuenta", "La cuenta no cuadra", "Salió más de lo comprado, o Odoo lo cuenta en otra unidad."],
];

export function motivos(r: Fila): string[] {
  if (r.pc == null || r.nv || r.so || !((r._q ?? 0) > 0)) return [];
  const m: string[] = [];
  const v3 = v3de(r) || {};
  if (v3.rv) m.push("precio");
  if (!(r.rn || []).some((x) => x.av != null)) m.push("bodega");
  if (r.td || r.trv) m.push("titulo");
  if (r.tv === "n" || r.tfm === "n") m.push("foto");
  if (r.sin) m.push("sin_sku");
  if ((r.cm || []).includes("ia")) m.push("empate");
  if (!r.ml && !r.em) m.push("sin_precio");
  if (r.dm || r.uf) m.push("cuenta");
  return m;
}

export function pasa(D: Catalogo, r: Fila, E: Filtros): boolean {
  if (E.val) {
    const ms = r._mv;
    if (!ms.length || (E.val !== "*" && !ms.includes(E.val))) return false;
  }
  if (E.sku === "con" && r.sin) return false;
  if (E.sku === "sin" && !r.sin) return false;
  if (E.pub === "si" && !publicadoEnML(r)) return false;
  if (E.pub === "no" && publicadoEnML(r)) return false;
  if (E.q && r._b.indexOf(E.q) < 0) return false;
  if (E.cat && c1(D, r) !== E.cat) return false;
  if (E.sub && c2(D, r) !== E.sub) return false;
  if (E.con && !(r.ct && r.ct[E.con] != null) && String(r.ec) !== E.con) return false;
  if (E.est === "queda" && !((r._q ?? 0) > 0)) return false;
  if (E.est === "so" && !r.so) return false;
  if (E.est === "sin" && !r.sin) return false;
  if (E.est === "est" && r.pe == null) return false;
  if (E.est === "nopl" && (r.pc != null || r.pe != null)) return false;
  if (E.est === "dm" && !r.dm) return false;
  if (E.est === "uf" && !r.uf) return false;
  if (E.est === "noodoo" && !(r.pc != null && !r.sin && !r.en && !r.lb && !r.ps)) return false;
  if (E.pre === "ml" && !r.ml) return false;
  if (E.pre === "az" && !r.az) return false;
  if (E.pre === "dos" && !(r.ml && r.az)) return false;
  if (E.pre === "no" && (r.ml || r.az)) return false;
  if (E.pre === "az4" && !(r._fa && r.ml && r.ml.o === "e" && r.az!.m > 4 * r.ml.m)) return false;
  if (E.pre === "v3r" && !(v3de(r) || {}).rv) return false;
  if (E.pre === "v3m" && !(v3de(r) || {}).m) return false;
  if (E.pre.slice(0, 2) === "c-") {
    const kf = E.pre.slice(2);
    if (kf === "e" ? !(r._cm == null && r.em) : kf === "u" ? !(r.ml && r.ml.ue) : r._cm !== kf) return false;
  }
  if (E.emp && r._m !== E.emp) return false;
  return true;
}

export type Orden = "s" | "pc" | "ps" | "pq" | "lb" | "mlm" | "_tm" | "azm" | "_ta";

/**
 * Las tres prioridades que pidió Brandon (30-oct-2026): «ordenar de mayor retorno, mayor ticket, mayor stock».
 * Retorno = lo que queda × su precio de ML (`_tm`); ticket = precio de ML por pieza (`_pm`); stock = piezas que
 * quedan. La elegida manda y las otras dos desempatan, en ese orden. Sin costos en la pestaña, «retorno» es venta
 * esperada, no utilidad.
 */
export const PRIORIDADES: [Orden, string, string][] = [
  ["_tm", "Mayor retorno", "piezas que quedan × precio de ML"],
  ["mlm", "Mayor ticket", "precio de ML por pieza"],
  ["pq", "Mayor stock", "piezas que quedan"],
];

export function ordenar(l: Fila[], k: Orden, d: number): Fila[] {
  const val = (r: Fila, c: Orden): number | string | null | undefined =>
    c === "mlm" ? r._pm : c === "azm" ? r._pa : (r as unknown as Record<string, number | string | null>)[c];
  // Si se ordena por una prioridad, las otras dos desempatan (descendente).
  const desempate = PRIORIDADES.map((p) => p[0]).filter((c) => c !== k && PRIORIDADES.some((p) => p[0] === k));
  const cmp = (a: Fila, b: Fila, c: Orden, dir: number): number => {
    const x = val(a, c), y = val(b, c);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    if (typeof x === "string") return dir * (x < (y as string) ? -1 : x > (y as string) ? 1 : 0);
    return dir * ((x as number) - (y as number));
  };
  return l.sort((a, b) => {
    let r = cmp(a, b, k, d);
    for (const c of desempate) if (!r) r = cmp(a, b, c, -1);
    return r || (a.s < b.s ? -1 : 1);
  });
}

// ── Totales ──────────────────────────────────────────────────────────────────

interface Rango { n: number; s: number; lo: number; hi: number }
export interface Totales {
  n: number; sk: number; sin: number; pc: number; ps: number; pq: number; so: number; viv: number; lb: number; psx: number;
  pe: number; nest: number; nopl: number; lbx: number;
  vm: number; pzm: number; em: number; pem: number; nm: number; va: number; pza: number; ea: number; pea: number; na: number;
  xm: Rango; xa: Rango; vmp: number; vmb: number; vmk: number; tcm: number; vdp: number; tcd: number; tm: number; ta: number;
  cm: Record<string, { v: number; pz: number; n: number }>;
  /** Estimados por base (`emc`, `pat`…): [canal m|a][base] → valor y piezas. */
  est: Record<string, number>;
}

export function sumar(l: Fila[]): Totales {
  const t: Totales = {
    n: 0, sk: 0, sin: 0, pc: 0, ps: 0, pq: 0, so: 0, viv: 0, lb: 0, psx: 0, pe: 0, nest: 0, nopl: 0, lbx: 0,
    vm: 0, pzm: 0, em: 0, pem: 0, nm: 0, va: 0, pza: 0, ea: 0, pea: 0, na: 0,
    xm: { n: 0, s: 0, lo: Infinity, hi: -Infinity }, xa: { n: 0, s: 0, lo: Infinity, hi: -Infinity },
    vmp: 0, vmb: 0, vmk: 0, tcm: 0, vdp: 0, tcd: 0, tm: 0, ta: 0,
    cm: { f: { v: 0, pz: 0, n: 0 }, n: { v: 0, pz: 0, n: 0 }, r: { v: 0, pz: 0, n: 0 }, b: { v: 0, pz: 0, n: 0 }, k: { v: 0, pz: 0, n: 0 }, p: { v: 0, pz: 0, n: 0 } },
    est: {},
  };
  const T = t as unknown as Record<string, number>;
  const canal = (r: Fila, c: "m" | "a", v: number | null, e: number | null, est?: Estimado) => {
    const pq = r.pq ?? 0;
    if (v != null) {
      T["v" + c] += v;
      T["pz" + c] += pq;
      const px = c === "m" ? r.ml!.m : r.az!.m;
      const x = c === "m" ? t.xm : t.xa;
      x.n++; x.s += px; if (px < x.lo) x.lo = px; if (px > x.hi) x.hi = px;
      if (c === "m") {
        const f = t.cm[r._cm || "p"];
        if (f) { f.v += v; f.pz += pq; f.n++; }
        if (r._cm === "b") t.vmb += v; else if (r._cm === "k") t.vmk += v; else t.vmp += v;
      }
    } else if (e != null && est) {
      T["e" + c] += e;
      T["pe" + c] += pq;
      t.est["e" + c + est[1]] = (t.est["e" + c + est[1]] || 0) + e;
      t.est["p" + c + est[1]] = (t.est["p" + c + est[1]] || 0) + pq;
    } else T["n" + c] += pq;
  };
  l.forEach((r) => {
    t.n++;
    if (r.pc != null) {
      const pq = r.pq ?? 0;
      t.pc += r.pc; t.pq += pq; t.ps += Math.min(r.pz || r.ps || 0, r.pc);
      if (r.sin) t.sin++; else t.sk++;
      if (r.so) t.so++; else if (pq > 0) t.viv++;
      canal(r, "m", r._vm, r._em, r.em);
      if (r._pm != null && !r.nv) t.tcm += r.pc * r._pm;
      if (r._dp != null && !r.nv) { t.vdp += pq * r._dp; t.tcd += r.pc * r._dp; }
      canal(r, "a", r._va, r._ea, r.ea);
      t.lb += r.lb || 0;
    } else {
      t.psx += r.ps || 0; t.lbx += r.lb || 0;
      if (r.pe != null) { t.nest++; t.pe += r.pe; } else t.nopl++;
    }
  });
  t.tm = t.vm + t.em;
  t.ta = t.va + t.ea;
  return t;
}

export const partesML = (t: Totales, sep: string) =>
  [`del producto ${mill(t.vmp)}`, t.vmk ? `mercado sin marcas ${mill(t.vmk)}` : "", t.vmb ? `banda ${mill(t.vmb)}` : "", t.em ? `estimado ${mill(t.em)}` : ""]
    .filter(Boolean).join(sep);

// ── Por categoría ────────────────────────────────────────────────────────────

export interface Sub { n: string; l: Fila[]; t: Totales }
export interface Grupo { n: string; l: Fila[]; t: Totales; sub: Sub[] }

export function agrupar(D: Catalogo): Grupo[] {
  const g: Record<string, { n: string; subs: Record<string, { n: string; l: Fila[] }>; l: Fila[] }> = {};
  D.R.forEach((r) => {
    if (r.pc == null) return;
    const a = c1(D, r), b = c2(D, r) || "(sin subcategoría)";
    const x = g[a] || (g[a] = { n: a, subs: {}, l: [] });
    x.l.push(r);
    const y = x.subs[b] || (x.subs[b] = { n: b, l: [] });
    y.l.push(r);
  });
  return Object.values(g)
    .map((x) => ({
      n: x.n, l: x.l, t: sumar(x.l),
      sub: Object.values(x.subs).map((y) => ({ n: y.n, l: y.l, t: sumar(y.l) })).sort((a, b) => b.t.pq - a.t.pq),
    }))
    .sort((a, b) => b.t.pq - a.t.pq);
}

// ── Copiar para Excel ────────────────────────────────────────────────────────

export function tsv(D: Catalogo, vista: Fila[]): string {
  const cab = ["SKU", "Título", "Nombre original", "Categoría", "Subcategoría", "Comprado", "Salió", "Queda", "SOLD OUT", "Odoo libre",
    "min_meli", "media_meli", "mediana_meli", "max_meli", "publicaciones_meli", "precio_meli_usado", "precio_meli_depurado_eduardo", "fuente_meli", "valor_meli",
    "min_amz", "media_amz", "max_amz", "publicaciones_amz", "precio_amz_usado", "amz_medido_o_estimado", "valor_amz",
    "Contenedores", "Empate", "Foto", "Publicado en ML"];
  const enML = (r: Fila) => (r.sin ? "" : r._pub == null ? "sin dato"
    : r._pub.length ? r._pub.map(([c, s]) => `${c} ${s === "active" ? "activa" : "pausada"}`).join("; ") : "no");
  const tipo = (f: boolean, e: Estimado | undefined, r: Fila) => (f ? "medido" : e ? `estimado: ${comoSeEstimo(e)}` : r.nv ? "insumo, no se valúa" : "");
  const canal = (p: Precio | undefined, f: boolean, e: Estimado | undefined, usado: number | null, valor: number | null, r: Fila) =>
    [p ? p.lo : "", p ? p.m : "", p ? p.hi : "", p ? p.n : "", usado != null ? usado : "", tipo(f, e, r), valor != null ? Math.round(valor) : ""];
  const meli = (r: Fila) => {
    const p = r.ml;
    if (!(p && p.o === "e")) {
      return [p ? p.lo : "", p ? p.m : "", p ? p.d : "", p ? p.hi : "", p ? p.n : "", r._pm != null ? r._pm : "", "",
        r._cm ? FML[r._cm] : tipo(false, r.em, r), r._tm != null ? Math.round(r._tm) : ""];
    }
    return [p.lo ?? "", p.me ?? "", p.d ?? "", p.hi ?? "", p.n || "", p.m, p.dp,
      FML[p.cl || ""] + (p.tp ? " · topado" : "") + (p.pk ? ` · paquete de ${p.pk}` : ""), r._tm != null ? Math.round(r._tm) : ""];
  };
  const out = [cab.join("\t")];
  vista.forEach((r) => {
    out.push([r.sin ? "(sin SKU)" : r.s, r.t, r.n || "", c1(D, r), c2(D, r), r.pc != null ? r.pc : "", r.pz || r.ps || 0,
      r.pc != null ? r.pq : "", r.so ? "sí" : "", r.lb || 0]
      .concat(meli(r) as (string | number)[], canal(r.az, r._fa, r.ea, r._pa, r._ta, r) as (string | number)[],
        [Object.keys(r.ct || {}).join(" "), (r.cm || []).join(" "), r.fo === "pl" ? "packing list" : r.fo === "odoo" ? "Odoo" : "", enML(r)])
      .map((v) => String(v ?? "").replace(/[\t\n]/g, " ")).join("\t"));
  });
  return out.join("\n");
}
