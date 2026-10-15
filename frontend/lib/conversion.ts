/**
 * Conversión de publicaciones — tipos y reglas puras (Análisis › Conversión).
 *
 * El backend (`routers/conversion.py`) entrega hechos: visitas, piezas, precio,
 * stock y la medición anterior. Aquí se decide el cuadrante y las alertas, con
 * umbrales que cada quien mueve en su navegador (localStorage), igual que las
 * reglas del Monitor de precios FULL.
 *
 * ⚠️ Los umbrales de REGLAS_ORIGINALES son supuestos del diseño (15-oct-2026)
 * y Eduardo no los ha confirmado, salvo «conversión ≥ 5 % y stock para ≤ 9 días»,
 * que viene de sus reglas de precio de FULL.
 *
 * `visitas === null` significa «sin medición» (el canal no da visitas o ML
 * todavía no la midió). Nunca se trata como cero.
 */

export interface PubConversion {
  listing_id?: string;
  sku: string;
  cuenta: string;
  titulo: string | null;
  situacion?: string | null;
  full?: boolean;
  categoria?: string | null;
  categoria_raiz?: string | null;
  precio?: number | null;
  precio_lista?: number | null;
  stock?: number | null;
  visitas: number | null;
  visitas_at?: string | null;
  visitas_ant?: number | null;
  visitas_ant_at?: string | null;
  piezas: number;
  ingreso: number;
  canceladas: number;
  piezas_ant: number | null;
  piezas_14d?: number;
  piezas_por_dia?: number[];
}

export interface RespConversion {
  canal: string;
  tiene_visitas: boolean;
  generado_at: string;
  publicaciones: PubConversion[];
}

export interface VentaDia { dia: string; precio: number | null; cancelada: boolean; piezas: number }
export interface VisitaDia { fecha: string; visitas: number }

export interface RespDetalle {
  publicacion: PubConversion;
  dias: number;
  desde: string;
  ventas: VentaDia[];
  visitas: VisitaDia[] | null;
  visitas_fuente: "kubera" | "mercado_libre" | null;
  visitas_aviso: string | null;
  calidad: { valor: number | null; nivel: string | null; medido_en: string | null } | null;
}

export interface Reglas {
  visitasAltas: number;     // visitas en 30 d desde las que una publicación tiene «mucho tráfico»
  convMeta: number;         // conversión que separa los cuadrantes (fracción, 0.03 = 3 %)
  minVisitas: number;       // por debajo, muy pocas visitas para juzgar la conversión
  convBaja: number;         // «casi sin ventas» = conversión menor a esto, con mucho tráfico
  caidaConv: number;        // «la conversión cayó» = esta fracción menos que la medición anterior
  caidaVisitas: number;     // «las visitas bajaron» = esta fracción menos que la medición anterior
  minVisitasCaida: number;  // visitas mínimas para juzgar una caída
  convAgota: number;        // «se agota y convierte bien»: conversión mínima…
  diasAgota: number;        // …y stock para estos días o menos
}

export const REGLAS_ORIGINALES: Reglas = {
  visitasAltas: 500, convMeta: 0.03, minVisitas: 30, convBaja: 0.01,
  caidaConv: 0.3, caidaVisitas: 0.4, minVisitasCaida: 100,
  convAgota: 0.05, diasAgota: 9,
};

const CLAVE_REGLAS = "conversion.reglas.v1";

export function leerReglas(): Reglas {
  try {
    const crudo = window.localStorage.getItem(CLAVE_REGLAS);
    if (crudo) return { ...REGLAS_ORIGINALES, ...JSON.parse(crudo) };
  } catch { /* sin almacenamiento: las originales */ }
  return REGLAS_ORIGINALES;
}

export function guardarReglas(r: Reglas): void {
  try { window.localStorage.setItem(CLAVE_REGLAS, JSON.stringify(r)); } catch { /* nada */ }
}

export type Cuadrante = "gan" | "traf" | "joya" | "dorm" | "chica" | "sin";
export type ClaveAlerta = "visitas" | "cayo" | "agota" | "sinvis" | "bajan";

export const CUADRANTES: Record<Cuadrante, { nombre: string; color: string; hacer: string }> = {
  gan:   { nombre: "Ganadoras", color: "#4f46e5", hacer: "Cuidar que no se agoten; probar subir el precio poco a poco." },
  traf:  { nombre: "Tráfico sin venta", color: "#ea580c", hacer: "La gente llega y no compra: precio, fotos, ficha o envío." },
  joya:  { nombre: "Joyas ocultas", color: "#0d9488", hacer: "Convierten bien pero casi nadie las ve: publicidad, catálogo o título." },
  dorm:  { nombre: "Dormidas", color: "#94a3b8", hacer: "Ni las ven ni las compran: decidir si se trabajan o se liquidan." },
  chica: { nombre: "Muy pocas visitas", color: "#94a3b8", hacer: "Muy pocas visitas para juzgar la conversión." },
  sin:   { nombre: "Sin medición", color: "#cbd5e1", hacer: "No hay medición de visitas." },
};

export const ALERTAS: Record<ClaveAlerta, { titulo: string; revisar: string }> = {
  visitas: { titulo: "Muchas visitas, casi sin ventas", revisar: "Revisar precio contra la competencia, fotos y ficha." },
  cayo:    { titulo: "La conversión cayó", revisar: "¿Qué cambió? Precio, envío, reseñas o un rival nuevo." },
  agota:   { titulo: "Se agota y convierte bien", revisar: "Reabastecer FULL o subir el precio con cuidado." },
  sinvis:  { titulo: "Casi sin visitas", revisar: "Revisar título, categoría y si entra al catálogo." },
  bajan:   { titulo: "Las visitas bajaron", revisar: "¿Perdió el catálogo, la promoción o la posición?" },
};

export interface Alerta { k: ClaveAlerta; texto: string }

export interface Metricas {
  conv: number | null;
  convAnt: number | null;
  ventasDia: number;
  dias: number;            // días que alcanza el stock al ritmo de hoy (Infinity sin ventas)
  cuadrante: Cuadrante;
  alertas: Alerta[];
}

export function metricas(p: PubConversion, R: Reglas): Metricas {
  const v = p.visitas;
  const conv = v != null && v > 0 ? p.piezas / v : null;
  const convAnt = p.visitas_ant != null && p.visitas_ant > 0 && p.piezas_ant != null
    ? p.piezas_ant / p.visitas_ant : null;
  // Mismo criterio que el Monitor FULL: con 3 o más piezas en 14 días, el ritmo
  // reciente; si no, el de 30 días (con pocas ventas, 14 días es puro ruido).
  const p14 = p.piezas_14d ?? 0;
  const ventasDia = p14 >= 3 ? p14 / 14 : p.piezas / 30;
  const dias = ventasDia > 0 && p.stock != null ? p.stock / ventasDia : Infinity;

  let cuadrante: Cuadrante;
  if (v == null) cuadrante = "sin";
  else if (v < R.minVisitas) cuadrante = "chica";
  else if (v >= R.visitasAltas) cuadrante = (conv ?? 0) >= R.convMeta ? "gan" : "traf";
  else cuadrante = (conv ?? 0) >= R.convMeta ? "joya" : "dorm";

  const alertas: Alerta[] = [];
  if (v != null) {
    if (v >= R.visitasAltas && (conv ?? 0) < R.convBaja)
      alertas.push({ k: "visitas", texto: ALERTAS.visitas.titulo });
    if (v >= R.minVisitasCaida && convAnt != null && convAnt > 0 && (conv ?? 0) < convAnt * (1 - R.caidaConv))
      alertas.push({ k: "cayo", texto: `Conversión −${Math.round((1 - (conv ?? 0) / convAnt) * 100)} %` });
    if (conv != null && conv >= R.convAgota && dias <= R.diasAgota && (p.stock ?? 0) > 0)
      alertas.push({ k: "agota", texto: `Se agota en ${Math.max(1, Math.round(dias))} días` });
    if (v < R.minVisitas && (p.stock ?? 0) > 0 && p.situacion === "active")
      alertas.push({ k: "sinvis", texto: ALERTAS.sinvis.titulo });
    if (p.visitas_ant != null && p.visitas_ant >= R.minVisitasCaida && v < p.visitas_ant * (1 - R.caidaVisitas))
      alertas.push({ k: "bajan", texto: `Visitas −${Math.round((1 - v / p.visitas_ant) * 100)} %` });
  }
  return { conv, convAnt, ventasDia, dias, cuadrante, alertas };
}

/** Piezas que faltan para llegar a la meta con el tráfico que ya tiene: ordena la bandeja. */
export function piezasPerdidas(p: PubConversion, R: Reglas): number {
  return p.visitas == null ? 0 : p.visitas * R.convMeta - p.piezas;
}

export const num = (x: number | null | undefined) =>
  x == null ? "—" : Math.round(x).toLocaleString("es-MX");
export const pct = (x: number | null | undefined, dec = 1) =>
  x == null ? "—" : `${(x * 100).toFixed(dec)} %`;
export const pesos = (x: number | null | undefined) =>
  x == null ? "—" : `$${Math.round(x).toLocaleString("es-MX")}`;
export const fechaCorta = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  return d.toLocaleDateString("es-MX", { day: "numeric", month: "short", timeZone: "America/Mexico_City" });
};
export const urlML = (listingId: string) =>
  `https://articulo.mercadolibre.com.mx/${listingId.replace(/^MLM/, "MLM-")}`;
