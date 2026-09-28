/**
 * Genera `fixtures/*.json`: datos de PRUEBA para desarrollar la web sin backend.
 *
 * NO son datos de producción ni se derivan de ellos: SKUs, títulos y cifras son
 * inventados con una semilla fija (mismo resultado en cada corrida). Lo que sí se
 * respeta al pie de la letra es la FORMA de `sandbox_precios/DISENO.md` §6 y la
 * aritmética del §4-5 (comisión por tramo sobre precio con IVA, escalón de envío en
 * $299, U(P) = U0·(P/P0)^β, punto óptimo = el menor P con Π ≥ 0.9·Πmax y margen ≥
 * piso). Así la curva, la tabla y el cajón cuentan la misma historia y un error de
 * pintado se nota.
 *
 * Los órdenes de magnitud salen de lo medido en los mapas de exploración
 * (28-sep-2026): 2,679 publicaciones de BEKURA y 2,695 de SANCORFASHION, 599
 * activas en ML, 1,412 FULL pausadas casi sin stock en FULL, Amazon congelado el
 * 18-sep y Walmart el 17-ago.
 *
 * Uso:  node scripts/generar-fixtures.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const aqui = path.dirname(fileURLToPath(import.meta.url));
const destino = path.join(aqui, "..", "fixtures");
fs.mkdirSync(destino, { recursive: true });

// ── Azar reproducible ───────────────────────────────────────────────────────
let semilla = 20260928;
function azar() {
  semilla |= 0; semilla = (semilla + 0x6d2b79f5) | 0;
  let t = Math.imul(semilla ^ (semilla >>> 15), 1 | semilla);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
const entre = (a, b) => a + (b - a) * azar();
const entero = (a, b) => Math.floor(entre(a, b + 1));
const elige = (xs) => xs[Math.floor(azar() * xs.length)];
const r2 = (x) => (x === null ? null : Math.round(x * 100) / 100);
const r4 = (x) => (x === null ? null : Math.round(x * 10000) / 10000);
const normal = () => { let u = 0, v = 0; while (!u) u = azar(); while (!v) v = azar(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };

const HOY = "2026-09-28";
const GENERADO = "2026-09-28T12:40:00Z";
const diaMas = (iso, n) => { const d = new Date(`${iso}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

const parametros = JSON.parse(fs.readFileSync(path.join(aqui, "..", "..", "..", "backend", "sandbox_precios", "parametros.json"), "utf8"));
const OPT = parametros.optimizador;

// ── Catálogo inventado ──────────────────────────────────────────────────────
const CATALOGO = [
  ["TEC", "Lámpara LED de escritorio recargable táctil", "MLM1582", 0.35],
  ["ORG", "Organizador de cajones 6 piezas plástico transparente", "MLM1631", 0.6],
  ["COC", "Set 3 sartenes de hierro fundido prehorneadas", "MLM1574", 4.2],
  ["ORG", "Vasos reutilizables con tapa y popote 4 piezas", "MLM1621", 0.5],
  ["TEC", "Audífonos inalámbricos Bluetooth 5.3 con estuche de carga", "MLM1276", 0.18],
  ["TEC", "Soporte para laptop de aluminio ajustable plegable", "MLM1648", 0.9],
  ["EST", "Repisa flotante de madera 60 cm juego de 3", "MLM1644", 3.1],
  ["ACC", "Mochila antirrobo con puerto USB impermeable", "MLM1227", 0.8],
  ["COC", "Termo de acero inoxidable 1 L doble pared", "MLM1574", 0.55],
  ["ORG", "Tapete antiderrapante para baño memory foam", "MLM1631", 0.7],
  ["MAN", "Cortina blackout térmica 2 paneles con argollas", "MLM1631", 1.6],
  ["COC", "Báscula digital de cocina 5 kg acero", "MLM1574", 0.45],
  ["ACC", "Cepillo alisador eléctrico cerámica iónica", "MLM1246", 0.6],
  ["ACC", "Faros de niebla LED universales 2 piezas", "MLM1747", 1.1],
  ["TEC", "Mini proyector portátil 1080p WiFi", "MLM1000", 1.4],
  ["ORG", "Organizador de zapatos 10 niveles metálico", "MLM1631", 2.4],
  ["COC", "Juego de cuchillos 6 piezas con base de madera", "MLM1574", 1.5],
  ["COC", "Lonchera térmica con 3 contenedores de vidrio", "MLM1574", 1.3],
  ["TEC", "Humidificador ultrasónico 300 ml con luz", "MLM1276", 0.4],
  ["TEC", "Bocina Bluetooth resistente al agua IPX7", "MLM1000", 0.6],
  ["TEC", "Reloj despertador digital con espejo LED", "MLM1276", 0.5],
  ["EST", "Perchero de pie metálico con 3 niveles", "MLM1644", 3.8],
  ["ORG", "Botella de agua motivacional 2 L con marcas de tiempo", "MLM1621", 0.35],
  ["ACC", "Kit de brochas de maquillaje 12 piezas con estuche", "MLM1246", 0.3],
  ["TEC", "Cable USB-C carga rápida 2 m paquete de 3", "MLM1051", 0.15],
  ["TEC", "Soporte de celular para auto magnético", "MLM1747", 0.2],
  ["TEC", "Tira LED RGB 5 m con control y app", "MLM1276", 0.3],
  ["ORG", "Canasta organizadora plegable de tela 3 piezas", "MLM1631", 0.9],
  ["MAN", "Almohada ortopédica memory foam cervical", "MLM1631", 1.2],
  ["COC", "Sartén antiadherente 28 cm con tapa de vidrio", "MLM1574", 1.7],
  ["COC", "Molino de café manual con muelas cerámicas", "MLM1574", 0.7],
  ["ORG", "Pizarrón magnético 60 x 40 cm con marcadores", "MLM1631", 1.9],
  ["TEC", "Cámara de seguridad WiFi 1080p visión nocturna", "MLM1000", 0.5],
  ["MAN", "Ventilador de torre 36 pulgadas silencioso", "MLM1574", 5.6],
  ["EST", "Mesa plegable para laptop con ventilador", "MLM1644", 2.8],
  ["TEC", "Aspiradora de mano inalámbrica 120 W", "MLM1574", 1.8],
  ["CALZ", "Tenis deportivos para dama suela ligera", "MLM1430", 0.8],
  ["ACC", "Bolsa tote de lona con cierre y bolsillos", "MLM1430", 0.4],
  ["MAN", "Espejo de pared redondo 50 cm marco metálico", "MLM1631", 3.4],
  ["COC", "Escurridor de platos 2 niveles acero", "MLM1574", 2.1],
  ["ORG", "Dispensador de jabón automático recargable", "MLM1631", 0.45],
  ["VIA", "Maleta de mano 20 pulgadas rígida con TSA", "MLM1227", 3.2],
];
const COLORES = ["NEG", "BLN", "ROS", "MUL", "PLA", "GRS", "AZU", "VER", "DOR", "CAF"];

// ── Economía (DISENO §4) ────────────────────────────────────────────────────
const tramoPct = (p) => (p < 299 ? 0.195 : p < 500 ? 0.18 : p < 1000 ? 0.16 : 0.15);
/** Envío FULL: bajo $299 el vendedor paga una tarifa reducida; desde $299, el envío gratis completo. */
const envioFull = (p, kg) => { const completo = 72 + 26 * Math.max(kg, 0.3); return p < 299 ? Math.round(completo * 0.42) : Math.round(completo); };
const iva = (p) => p - p / 1.16;
function economia(p, costo, kg) {
  const pctc = tramoPct(p);
  const com = p * pctc; const env = envioFull(p, kg); const i = iva(p);
  const util = costo === null ? null : p - com - env - i - costo;
  return { comision_pct: pctc, comision: com, envio: env, iva: i, utilidad: util, margen: util === null ? null : util / p };
}

// ── Contenedores y packing ──────────────────────────────────────────────────
const CONTENEDORES = [
  { codigo: "PCIU9516451", nota: "contenedor 33", total_cbm: 66.42 },
  { codigo: "MSKU7781230", nota: "contenedor 34", total_cbm: 61.08 },
  { codigo: "TGHU6634102", nota: "contenedor 35", total_cbm: 70.9 },
  { codigo: "CMAU4512876", nota: "contenedor 36", total_cbm: 58.37 },
  { codigo: "OOLU8820341", nota: "contenedor 37", total_cbm: 81.6 },
];
const CONTENEDOR_MXN = parametros.contenedor.costo_mxn;

// Cada producto recibe su volumen por pieza (derivado del peso para que sea coherente).
const productos = CATALOGO.map(([pref, titulo, cat, kg], i) => {
  const sku = `${pref}-${String(entero(100, 1990)).padStart(4, "0")}-${elige(COLORES)}`;
  const cbm = r4(Math.max(0.0012, kg * entre(0.0035, 0.009)));
  const cont = CONTENEDORES[i % CONTENEDORES.length];
  const costoVol = (CONTENEDOR_MXN * cbm) / cont.total_cbm;
  const fuente = i % 7 === 6 ? "sin_costo" : i % 3 === 0 ? "packing_list_exacto" : i % 3 === 1 ? "prorrateo_kubera" : "tarifa_7500";
  const costo = fuente === "sin_costo" ? null : fuente === "tarifa_7500" ? cbm * 7500 : costoVol * entre(0.97, 1.03);
  // Precio: markup sobre el costo; algunos a propósito pierden dinero.
  const base = Math.max(79, (costo ?? costoVol) * entre(2.1, 5.2) + 45 + kg * 18);
  const precio = Math.round(base / 10) * 10 - 1;
  return { i, sku, titulo, cat, kg, cbm, cont, costoVol, fuente, costo: costo === null ? null : r2(costo), precio, usd: azar() < 0.6 ? r2(entre(0.6, 18) * (1 + kg)) : null };
});

// ── Publicaciones ───────────────────────────────────────────────────────────
const publicaciones = [];
let idListing = 2703304601;
const nuevoListing = () => `MLM${idListing += entero(1000, 900000)}`;

function competenciaPara(p, forzar = false) {
  if (!forzar && azar() < 0.25) return null;
  const fuente = azar() < 0.7 ? "serp" : azar() < 0.5 ? "bestsellers" : "sugerido_ml";
  const centro = p * entre(0.72, 1.35);
  const n = fuente === "sugerido_ml" ? 1 : entero(5, 24);
  const lista = [];
  for (let k = 0; k < Math.min(n, 8); k++) {
    lista.push({ titulo: null, precio: r2(Math.max(39, centro * Math.exp(normal() * 0.18))), vendedor: `vendedor_${entero(1000, 9999)}`, vendidos: entero(0, 5000) });
  }
  lista.sort((a, b) => a.precio - b.precio);
  const precios = lista.map((x) => x.precio);
  const mediana = precios.length ? precios[Math.floor(precios.length / 2)] : null;
  return {
    n,
    promedio: r2(precios.reduce((a, b) => a + b, 0) / Math.max(1, precios.length)),
    mediana: r2(mediana),
    minimo: r2(Math.min(...precios)),
    maximo: r2(Math.max(...precios)),
    fuente,
    capturado_en: `${diaMas(HOY, -entero(0, 20))}T0${entero(1, 9)}:15:00Z`,
    sugerido_ml: azar() < 0.4 ? r2(centro * 0.93) : null,
    lista: fuente === "sugerido_ml" ? [] : lista,
  };
}

function filaML(prod, cuenta, tipo) {
  const listing_id = nuevoListing();
  const esFull = tipo === "activa_full" || tipo === "pausada_full";
  const estado = tipo.startsWith("activa") ? "activa" : tipo === "revision" ? "en_revision" : "pausada";
  const promo = azar() < 0.35 && estado === "activa" ? { tipo: elige(["custom", "deal", "price_discount"]), fin: `${diaMas(HOY, entero(2, 40))}T05:59:59Z` } : null;
  const precioCobrado = prod.precio;
  const precioLista = promo ? Math.round((precioCobrado * entre(1.25, 2.6)) / 10) * 10 - 1 : precioCobrado;
  const e = economia(precioCobrado, prod.costo, prod.kg);
  const visitas = estado === "activa" ? entero(180, 9200) : entero(0, 400);
  const cr = entre(0.006, 0.11);
  const unidades = estado === "activa" ? Math.round(visitas * cr) : estado === "pausada" ? entero(0, 6) : 0;
  const stockFull = tipo === "activa_full" ? entero(3, 260) : tipo === "pausada_full" ? (azar() < 0.05 ? entero(1, 4) : 0) : 0;
  const stockOdoo = azar() < 0.72 ? entero(0, 900) : null;
  const avisos = [];
  if (prod.fuente === "sin_costo") avisos.push("sin_costo");
  return {
    id: `mercado_libre:${cuenta}:${listing_id}`,
    canal: "mercado_libre", cuenta, listing_id, sku: prod.sku, titulo: prod.titulo,
    url: `https://articulo.mercadolibre.com.mx/${listing_id.replace("MLM", "MLM-")}`,
    thumbnail: null, categoria_id: prod.cat,
    estado, situacion: estado === "activa" ? "active" : estado === "en_revision" ? "under_review" : "paused",
    sub_status: tipo === "pausada_full" && stockFull === 0 ? ["out_of_stock"] : [],
    logistica: esFull ? "fulfillment" : "xd_drop_off", es_full: esFull,
    precio_cobrado: precioCobrado, precio_lista: precioLista, promo,
    stock_full: esFull ? stockFull : null, stock_propio: esFull ? 0 : entero(0, 80), stock_odoo: stockOdoo,
    costo: {
      unitario: prod.costo, fuente: prod.fuente,
      contenedor: prod.fuente === "sin_costo" || prod.fuente === "tarifa_7500" ? null : prod.cont.codigo,
      validado: prod.fuente !== "sin_costo" && azar() < 0.45,
      revisado_por: prod.fuente !== "sin_costo" && azar() < 0.45 ? elige(["Andrea", "Thalia", "Cinthya", "Brandon"]) : null,
      costo_panel: prod.costo === null ? null : r2(prod.costo * entre(1.4, 6.5)),
    },
    comision_pct: e.comision_pct, comision: r2(e.comision), envio: e.envio, iva: r2(e.iva),
    utilidad: r2(e.utilidad), margen_pct: r4(e.margen),
    visitas_30d: visitas, unidades_30d: unidades, conversion_30d: visitas ? r4(unidades / visitas) : null,
    ingreso_30d: r2(unidades * precioCobrado),
    competencia: competenciaPara(precioCobrado),
    tags_calidad: azar() < 0.2 ? ["poor_quality_thumbnail"] : [],
    frescura_at: `${HOY}T1${entero(0, 2)}:${entero(10, 59)}:00Z`,
    avisos, supuesto_canal: false,
    _prod: prod,
  };
}

const tiposML = ["activa_full", "activa_full", "activa_full", "pausada_full", "pausada_full", "activa_nofull", "pausada_nofull", "revision"];
productos.forEach((prod, i) => {
  const cuentas = i % 4 === 0 ? ["BEKURA", "SANCORFASHION"] : [i % 2 ? "SANCORFASHION" : "BEKURA"];
  for (const c of cuentas) publicaciones.push(filaML(prod, c, tiposML[(i + (c === "SANCORFASHION" ? 3 : 0)) % tiposML.length]));
});
// Recortar a 44 de ML para dejar sitio a los otros canales (la carga total queda en 60).
const ml = publicaciones.splice(0, 44);
publicaciones.length = 0;
publicaciones.push(...ml);

function filaOtroCanal(prod, canal, cuenta, idx) {
  const cfg = parametros.canales[canal];
  const listing_id = canal === "amazon" ? `B0${entero(10000000, 99999999)}` : canal === "walmart" ? `${entero(100000000, 999999999)}` : canal === "temu" ? `${entero(600000000000, 699999999999)}` : `17${entero(10000000000, 99999999999)}`;
  const precio = canal === "temu" ? Math.round(prod.precio * 0.62) : prod.precio + (canal === "amazon" ? 20 : 0);
  const com = precio * cfg.comision;
  const i = canal === "temu" ? 0 : iva(precio);
  const util = prod.costo === null ? null : precio - com - cfg.envio_unitario - i - prod.costo;
  const avisos = [];
  if (canal === "amazon") avisos.push("amazon_sin_cambios_desde_18_sep");
  if (canal === "walmart") avisos.push("walmart_sin_cambios_desde_17_ago");
  if (canal === "tiktok") avisos.push("tiktok_precio_puede_venir_sin_iva");
  const estado = idx % 3 === 2 ? "inactiva" : "activa";
  return {
    id: `${canal}:${cuenta}:${listing_id}`, canal, cuenta, listing_id, sku: prod.sku, titulo: prod.titulo,
    url: null, thumbnail: null, categoria_id: null,
    estado, situacion: estado === "activa" ? "active" : "inactive", sub_status: [],
    logistica: canal === "amazon" ? (idx % 4 === 0 ? "AFN" : "MFN") : canal === "walmart" ? "seller" : "drop",
    es_full: false, precio_cobrado: precio, precio_lista: precio, promo: null,
    stock_full: null, stock_propio: entero(0, 40), stock_odoo: azar() < 0.8 ? entero(0, 600) : null,
    costo: { unitario: prod.costo, fuente: prod.fuente, contenedor: null, validado: false, revisado_por: null, costo_panel: null },
    comision_pct: cfg.comision, comision: r2(com), envio: cfg.envio_unitario, iva: r2(i),
    utilidad: r2(util), margen_pct: util === null ? null : r4(util / precio),
    visitas_30d: null, unidades_30d: estado === "activa" ? entero(0, 40) : 0, conversion_30d: null,
    ingreso_30d: null, competencia: null, tags_calidad: [],
    frescura_at: canal === "amazon" ? "2026-09-18T19:02:00Z" : canal === "walmart" ? "2026-08-17T15:44:00Z" : `${HOY}T11:30:00Z`,
    avisos, supuesto_canal: true,
  };
}
[["amazon", "AMAZON", 6], ["walmart", "WALMART", 4], ["tiktok", "KUBERA", 3], ["temu", "TEMU", 3]].forEach(([canal, cuenta, n]) => {
  for (let k = 0; k < n; k++) publicaciones.push(filaOtroCanal(productos[(k * 5 + canal.length) % productos.length], canal, cuenta, k));
});
for (const f of publicaciones) if (f.ingreso_30d !== null && f.unidades_30d !== null) f.ingreso_30d = r2(f.unidades_30d * f.precio_cobrado);

// ── Precios óptimos y curvas (DISENO §5) ────────────────────────────────────
const precios = [];
const curvas = {};
const historial = {};

function recomendar(pub) {
  const prod = pub._prod;
  const P0 = pub.precio_cobrado;
  const pausada = pub.estado === "pausada";
  // Base: los 28 días NO censurados. Una pausada hereda la demanda de antes de pausarse.
  const V0 = pausada ? entre(40, 900) : pub.visitas_30d / 30;
  const U0 = pausada ? V0 * entre(0.01, 0.08) : pub.unidades_30d / 30;
  const conf = elige(["alta", "media", "media", "baja"]);
  const beta = Math.max(OPT.elasticidad_min, Math.min(OPT.elasticidad_max, -1.6 + normal() * 0.9));
  const betaV = Math.max(-2, Math.min(-0.05, -0.5 + normal() * 0.25));
  const fuenteB = conf === "alta" ? "item" : conf === "media" ? "categoria" : elige(["global", "prior"]);
  const ref = pub.competencia?.mediana ?? pub.competencia?.sugerido_ml ?? null;

  const puntos = [];
  const grilla = new Set();
  for (let f = OPT.rejilla_min_factor; f <= OPT.rejilla_max_factor + 1e-9; f += OPT.rejilla_paso) grilla.add(Math.round(P0 * f * 100) / 100);
  for (const e of [298.99, 299, 499, 500, 999, 1000]) if (e >= P0 * OPT.rejilla_min_factor && e <= P0 * OPT.rejilla_max_factor) grilla.add(e);
  const lista = [...grilla].sort((a, b) => a - b);
  for (const p of lista) {
    const u = U0 * Math.pow(p / P0, beta);
    const v = V0 * Math.pow(p / P0, betaV);
    const e = economia(p, prod.costo, prod.kg);
    puntos.push({
      precio: r2(p), visitas_dia: r2(v), conversion: v ? r4(u / v) : null, unidades_dia: r2(u),
      utilidad_unit: r2(e.utilidad), utilidad_dia: e.utilidad === null ? null : r2(u * e.utilidad), margen_pct: r4(e.margen),
    });
  }
  const eA = economia(P0, prod.costo, prod.kg);
  const cobertura = pub.stock_full && U0 > 0 ? pub.stock_full / U0 : pausada ? 0 : null;
  const razones = [];
  if (pausada) razones.push("pausada_sin_stock");
  if (pub.promo) razones.push("promo_vigente");
  if (conf === "baja") razones.push("elasticidad_baja_confianza");
  if (prod.costo === null) {
    razones.push("sin_costo");
    return { puntos, marcadores: { actual: P0, recomendado: null, piso: null, equilibrio: null, max_utilidad: null, ref_competencia: ref }, rec: {
      precio_recomendado: null, cambio_pct: null, precio_equilibrio: null, precio_piso: null, precio_max_utilidad: null, precio_max_volumen: null,
      U0, V0, beta, betaV, conf, fuenteB, ref, razones, cobertura, uR: null, vR: null, utilA: null, utilR: null, mA: null, mR: null,
    } };
  }
  const equilibrio = puntos.find((q) => q.utilidad_unit !== null && q.utilidad_unit >= 0)?.precio ?? null;
  const piso = puntos.find((q) => q.margen_pct !== null && q.margen_pct >= OPT.piso_margen)?.precio ?? null;
  const maxU = puntos.reduce((m, q) => (q.utilidad_dia !== null && (m === null || q.utilidad_dia > m.utilidad_dia) ? q : m), null);
  let sacrificio = OPT.sacrificio_utilidad_max;
  if (cobertura !== null && cobertura > OPT.cobertura_dias_exceso) { sacrificio = OPT.sacrificio_liquidacion; razones.push("stock_excesivo"); }
  if (cobertura !== null && cobertura < OPT.cobertura_dias_escasez && !pausada) { sacrificio = 0; razones.push("stock_escaso"); }
  let rec = null;
  if (maxU && maxU.utilidad_dia > 0) {
    rec = puntos.find((q) => q.utilidad_dia !== null && q.utilidad_dia >= (1 - sacrificio) * maxU.utilidad_dia && q.margen_pct >= OPT.piso_margen)?.precio ?? null;
  }
  if (rec !== null && ref !== null) {
    const [lo, hi] = OPT.banda_competencia;
    rec = Math.max(piso ?? 0, Math.min(Math.max(rec, lo * ref), hi * ref));
  }
  if (rec !== null) {
    // Terminación psicológica en 9 sin bajar del piso.
    const c = Math.round(rec / 10) * 10 - 1;
    rec = c >= (piso ?? 0) ? c : Math.ceil(rec);
  }
  if (eA.utilidad !== null && eA.utilidad < 0) razones.push("perdiendo_dinero");
  if (ref !== null && P0 > ref * 1.15) razones.push("sobre_competencia");
  if (ref !== null && P0 < ref * 0.85) razones.push("bajo_competencia");
  if ((P0 >= 290 && P0 <= 340) || (rec !== null && (P0 - 299) * (rec - 299) < 0)) razones.push("escalon_envio_299");
  if ((P0 >= 480 && P0 <= 560) || (rec !== null && (P0 - 500) * (rec - 500) < 0)) razones.push("tramo_comision_500");
  const eR = rec === null ? null : economia(rec, prod.costo, prod.kg);
  const uR = rec === null ? null : U0 * Math.pow(rec / P0, beta);
  const vR = rec === null ? null : V0 * Math.pow(rec / P0, betaV);
  return {
    puntos,
    marcadores: { actual: P0, recomendado: rec, piso, equilibrio, max_utilidad: maxU?.precio ?? null, ref_competencia: ref },
    rec: {
      precio_recomendado: rec, cambio_pct: rec === null ? null : (rec - P0) / P0, precio_equilibrio: equilibrio, precio_piso: piso,
      precio_max_utilidad: maxU?.precio ?? null, precio_max_volumen: piso,
      U0, V0, beta, betaV, conf, fuenteB, ref, razones, cobertura, uR, vR,
      utilA: U0 * eA.utilidad, utilR: eR === null ? null : uR * eR.utilidad, mA: eA.margen, mR: eR?.margen ?? null,
    },
  };
}

function plan(P0, rec, conf) {
  if (rec === null || Math.abs(rec - P0) / P0 < 0.005) return { modo: "semanal", pasos: [] };
  const pasos = [];
  let p = P0; let fecha = diaMas(HOY, 1);
  if (conf === "baja") {
    p = Math.round(P0 * 0.95 * 100) / 100;
    pasos.push({ fecha, precio: r2(p), nota: "prueba −5 %: medir una semana antes de seguir" });
    fecha = diaMas(fecha, 7);
  }
  for (let k = 0; k < 10 && Math.abs(p - rec) > 0.01; k++) {
    const tope = p * OPT.paso_max_semana;
    p = rec > p ? Math.min(rec, p + tope) : Math.max(rec, p - tope);
    pasos.push({ fecha, precio: r2(Math.abs(p - rec) < 0.01 ? rec : Math.round(p)) });
    fecha = diaMas(fecha, 7);
  }
  return { modo: "semanal", pasos };
}

for (const pub of publicaciones) {
  if (pub.canal !== "mercado_libre" || !pub.es_full) continue;
  const { puntos, marcadores, rec } = recomendar(pub);
  curvas[pub.id] = { puntos, marcadores };
  const U0 = rec.U0, V0 = rec.V0;
  precios.push({
    id: pub.id, sku: pub.sku, cuenta: pub.cuenta, listing_id: pub.listing_id, titulo: pub.titulo, estado: pub.estado,
    precio_actual: pub.precio_cobrado, precio_recomendado: rec.precio_recomendado, cambio_pct: r4(rec.cambio_pct),
    precio_equilibrio: rec.precio_equilibrio, precio_piso: rec.precio_piso, precio_max_utilidad: rec.precio_max_utilidad,
    precio_max_volumen: rec.precio_max_volumen, precio_ref_competencia: rec.ref, fuente_ref: pub.competencia?.fuente ?? null,
    unidades_dia: { actual: r2(U0), recomendado: r2(rec.uR) },
    visitas_dia: { actual: r2(V0), recomendado: r2(rec.vR) },
    conversion: { actual: V0 ? r4(U0 / V0) : null, recomendado: rec.vR ? r4(rec.uR / rec.vR) : null },
    utilidad_dia: { actual: r2(rec.utilA), recomendado: r2(rec.utilR) },
    margen: { actual: r4(rec.mA), recomendado: r4(rec.mR) },
    elasticidad: { beta: r2(rec.beta), beta_visitas: r2(rec.betaV), beta_conversion: r2(rec.beta - rec.betaV), fuente: rec.fuenteB,
                   n_semanas: rec.conf === "alta" ? entero(10, 21) : entero(0, 6), confianza: rec.conf },
    stock: { full: pub.stock_full, odoo: pub.stock_odoo, cobertura_dias: rec.cobertura === null ? null : r2(rec.cobertura) },
    razones: rec.razones, plan: plan(pub.precio_cobrado, rec.precio_recomendado, rec.conf), autorizacion: "pendiente",
  });

  // Historial de 150 días: precio ofrecido por tramos (promos que prenden y apagan),
  // realizado sólo en días con venta, y el recomendado sólo en los días con snapshot.
  const serie = [];
  let ofrecido = pub.precio_cobrado * entre(0.9, 1.25);
  const pausaDesde = pub.estado === "pausada" ? entero(10, 60) : 999;
  for (let d = 149; d >= 0; d--) {
    const fecha = diaMas(HOY, -d - 1);
    if (azar() < 0.05) ofrecido = pub.precio_cobrado * entre(0.8, 1.3);
    if (d < 20 && azar() < 0.2) ofrecido = pub.precio_cobrado;
    const pausado = d < pausaDesde ? false : false;
    const censurado = d < pausaDesde && pub.estado === "pausada";
    const temporada = 1 + 0.25 * Math.sin((150 - d) / 11);
    const v = censurado ? entero(0, 6) : Math.max(0, Math.round(V0 * temporada * Math.pow(ofrecido / pub.precio_cobrado, rec.betaV) * Math.exp(normal() * 0.25)));
    const lambda = censurado ? 0 : U0 * temporada * Math.pow(ofrecido / pub.precio_cobrado, rec.beta);
    const u = Math.max(0, Math.round(lambda + normal() * Math.sqrt(Math.max(lambda, 0.01))));
    const snap = d <= 2;
    serie.push({
      fecha,
      precio_realizado: u > 0 ? r2(ofrecido * entre(0.97, 1.0)) : null,
      unidades: u, visitas: v,
      precio_ofrecido: pausado ? null : r2(ofrecido),
      precio_recomendado: snap ? rec.precio_recomendado : null,
    });
    void pausado;
  }
  historial[pub.id] = { serie };
}

// ── Packing 100 (aquí 40) y contenedores ────────────────────────────────────
const packing = [];
const porCont = new Map();
productos.slice(0, 40).forEach((prod, i) => {
  const cont = prod.cont;
  const cajas = entero(4, 120);
  const piezasGrupo = elige([12, 20, 24, 36, 48, 60, 65, 100]);
  const mixta = azar() < 0.18;
  const cbmCaja = r4(prod.cbm * piezasGrupo);
  const kgPieza = r2(prod.kg * entre(0.9, 1.1));
  const vol = (CONTENEDOR_MXN * prod.cbm) / cont.total_cbm;
  const wm = (CONTENEDOR_MXN * Math.max(prod.cbm, kgPieza / 1000)) / (cont.total_cbm * entre(1.0, 1.08));
  const fob = prod.usd === null ? null : prod.usd * 19 * entre(1.6, 2.4);
  const costoProducto = prod.usd === null ? r2(entre(20, 900)) : r2(prod.usd * 19);
  const costoCbm = r2(prod.cbm * 7500);
  const pubsSku = publicaciones.filter((x) => x.sku === prod.sku);
  const precio = pubsSku[0]?.precio_cobrado ?? prod.precio;
  const mCon = economia(precio, vol, prod.kg).margen;
  const mPanel = economia(precio, costoProducto + costoCbm, prod.kg).margen;
  const metodo = i % 9 === 4 ? "dhash" : i % 11 === 7 ? "ia_titulo" : i % 5 === 1 ? "sha256" : "ferraforme";
  packing.push({
    sku: prod.sku, titulo: prod.titulo,
    cuentas: [...new Set(pubsSku.filter((x) => x.canal === "mercado_libre").map((x) => x.cuenta))],
    listing_ids: pubsSku.filter((x) => x.canal === "mercado_libre").map((x) => x.listing_id),
    precio_cobrado: precio, unidades_30d: pubsSku.reduce((a, x) => a + (x.unidades_30d ?? 0), 0),
    odoo: { container_numbers: `${cont.codigo}=CI^0PL ${cont.nota}`, codigos: [cont.codigo] },
    archivo: { nombre: `PL ${cont.codigo} ${cont.nota}.xlsx`, file_id: `1${Math.floor(azar() * 1e16).toString(36)}`, sha256: null, contenedor: cont.codigo },
    fila: entero(8, 460),
    empate: { metodo, distancia: metodo === "dhash" ? entero(0, 8) : metodo === "sha256" ? 0 : null, confianza: metodo === "ia_titulo" ? "baja" : metodo === "dhash" ? "media" : "alta" },
    cajas, piezas_fila: cajas * piezasGrupo, piezas_grupo: piezasGrupo, caja_mixta: mixta,
    cbm_caja: cbmCaja, cbm_pieza: prod.cbm, peso_pieza_kg: kgPieza, precio_usd: prod.usd,
    costos: {
      volumetrico_real: r2(vol), tarifa_fija_7500: r2(prod.cbm * 7500), peso_volumen_wm: r2(wm),
      valor_fob: fob === null ? null : r2(fob), hibrido_70_30: fob === null ? null : r2(0.7 * vol + 0.3 * fob),
    },
    kubera: azar() < 0.85 ? {
      costo_total: r2(costoProducto + costoCbm), costo_cbm: costoCbm, costo_producto: costoProducto,
      revisado_at: azar() < 0.5 ? `${diaMas(HOY, -entero(2, 40))}T17:20:00Z` : null,
      revisado_por: azar() < 0.5 ? elige(["Andrea", "Thalia", "Cinthya"]) : null, validado: azar() < 0.4,
    } : null,
    margen_con_525k: r4(mCon), margen_panel: r4(mPanel),
  });
  const agg = porCont.get(cont.codigo) ?? { skus: 0 };
  agg.skus += 1; porCont.set(cont.codigo, agg);
});
const contenedores = CONTENEDORES.map((c) => ({
  codigo: c.codigo, archivo: `PL ${c.codigo} ${c.nota}.xlsx`, sha256: null,
  total_cbm: c.total_cbm, total_piezas: entero(9000, 42000), total_peso_kg: r2(entre(8200, 24500)),
  total_usd: azar() < 0.7 ? r2(entre(18000, 64000)) : null,
  costo_m3: r2(CONTENEDOR_MXN / c.total_cbm),
  rango_ok: c.total_cbm >= parametros.contenedor.rango_m3_normal[0] && c.total_cbm <= parametros.contenedor.rango_m3_normal[1],
  renglones: entero(120, 520), renglones_sin_cbm: entero(0, 9), cajas_mixtas: entero(0, 30), skus_en_lote: porCont.get(c.codigo)?.skus ?? 0,
}));

// ── Métricas ────────────────────────────────────────────────────────────────
const serieDiaria = [];
for (let d = 59; d >= 0; d--) {
  const fecha = diaMas(HOY, -d - 1);
  const finde = [0, 6].includes(new Date(`${fecha}T12:00:00Z`).getUTCDay());
  for (const [cuenta, base] of [["BEKURA", 640], ["SANCORFASHION", 520]]) {
    const u = Math.round(base * (1 + 0.18 * Math.sin((60 - d) / 6)) * (finde ? 0.82 : 1) * Math.exp(normal() * 0.09));
    serieDiaria.push({ fecha, cuenta, unidades: u, ingreso: r2(u * entre(168, 212)), visitas: Math.round(u / entre(0.028, 0.041)) });
  }
}
const betas = precios.map((p) => p.elasticidad.beta).filter((b) => b !== null);
const histo = [];
for (let lo = -6; lo < -0.3 + 1e-9; lo += 0.5) {
  const hi = Math.min(-0.3, lo + 0.5);
  histo.push({ desde: r2(lo), hasta: r2(hi), n: Math.round(420 * Math.exp(-Math.pow((lo + 0.25 + 1.6) / 1.1, 2) / 2)) + betas.filter((b) => b >= lo && b < hi).length });
}
const metricas = {
  generado_at: GENERADO,
  por_cuenta: {
    BEKURA: { publicaciones: 2679, activas: 255, activas_full: 255, pausadas_full: 787, pausadas_full_sin_stock: 766, pausadas_full_con_stock_odoo: 431,
              visitas_dia: 18420, unidades_dia: 641.3, conversion: 0.0348, ingreso_30d: 3912400, margen_mediano: 0.142, perdiendo_dinero: 38, con_costo: 2211, con_competencia: 1386 },
    SANCORFASHION: { publicaciones: 2695, activas: 344, activas_full: 260, pausadas_full: 625, pausadas_full_sin_stock: 624, pausadas_full_con_stock_odoo: 352,
                     visitas_dia: 15230, unidades_dia: 522.7, conversion: 0.0343, ingreso_30d: 3104900, margen_mediano: 0.118, perdiendo_dinero: 51, con_costo: 2140, con_competencia: 1204 },
  },
  por_canal: {
    amazon: { publicaciones: 1693, activas: 1188, frescura: "2026-09-18T19:02:00Z" },
    walmart: { publicaciones: 235, activas: 171, frescura: "2026-08-17T15:44:00Z" },
    tiktok: { publicaciones: 1297, activas: 846, frescura: `${HOY}T11:30:00Z` },
    temu: { publicaciones: 517, activas: 402, frescura: `${HOY}T11:30:00Z` },
  },
  embudo: { visitas_30d: 1009500, unidades_30d: 34920 },
  elasticidad: {
    global: -1.62,
    por_categoria: [
      ["MLM1574", "Hogar, Muebles y Jardín", -1.84, 212], ["MLM1000", "Electrónica, Audio y Video", -2.31, 97],
      ["MLM1276", "Deportes y Fitness", -1.41, 64], ["MLM1631", "Organización del hogar", -1.72, 188],
      ["MLM1246", "Belleza y Cuidado Personal", -1.18, 71], ["MLM1747", "Accesorios para Vehículos", -1.95, 58],
      ["MLM1430", "Ropa, Bolsas y Calzado", -0.92, 83], ["MLM1227", "Equipaje y bolsos", -1.37, 22],
      ["MLM1051", "Celulares y Teléfonos", -2.66, 41], ["MLM1644", "Muebles", -1.49, 36],
    ].map(([categoria, nombre, beta, n]) => ({ categoria, nombre, beta, n })),
    histograma: histo,
  },
  palancas: publicaciones.filter((p) => p.canal === "mercado_libre" && p.estado === "pausada" && p.es_full)
    .map((p) => ({ tipo: "reactivar_full", id: p.id, sku: p.sku, titulo: p.titulo, cuenta: p.cuenta, listing_id: p.listing_id,
                   ventas_perdidas_dia: r2(entre(0.3, 14)), stock_odoo: p.stock_odoo ?? entero(20, 400),
                   motivo: "pausada por falta de stock en FULL con piezas libres en Odoo" }))
    .concat(Array.from({ length: 8 }, () => ({ tipo: "reactivar_full", sku: `${elige(["TEC", "ORG", "COC", "ACC"])}-${entero(100, 1990)}-${elige(COLORES)}`, titulo: null,
      cuenta: elige(["BEKURA", "SANCORFASHION"]), listing_id: nuevoListing(), ventas_perdidas_dia: r2(entre(0.2, 9)), stock_odoo: entero(5, 700),
      motivo: "pausada por falta de stock en FULL con piezas libres en Odoo" })))
    .sort((a, b) => b.ventas_perdidas_dia - a.ventas_perdidas_dia),
  serie_diaria: serieDiaria,
};

// ── Estado ──────────────────────────────────────────────────────────────────
const estado = {
  generado_at: GENERADO, version: "lab-0.1",
  etapas: {
    extraer_kubera: { ok: true, filas: 142318, duracion_s: 48.2, avisos: [] },
    extraer_ml: { ok: true, filas: 11842, duracion_s: 612.4, avisos: ["visitas: 3 items sin serie (404)"] },
    packing100: { ok: true, filas: 100, duracion_s: 181.9, avisos: ["17 SKUs saltados (ver packing_saltados.json)"] },
    costos_lab: { ok: true, filas: 13262, duracion_s: 6.1, avisos: [] },
    elasticidad: { ok: true, filas: 1927, duracion_s: 21.7, avisos: [] },
    optimizador: { ok: true, filas: 1927, duracion_s: 9.8, avisos: [] },
    construir: { ok: true, filas: 9116, duracion_s: 4.3, avisos: [] },
  },
  frescura: {
    ml_listings: `${HOY}T12:31:00Z`, amazon_listings: "2026-09-18T19:02:00Z", walmart_listings: "2026-08-17T15:44:00Z",
    competencia_serp: "2026-09-21T09:15:00Z", competencia_best: "2026-09-26T08:00:00Z", ventas: `${HOY}T12:20:00Z`, visitas_api: "2026-09-27",
  },
  contadores_ml_api: { get: 11842, "429": 3 },
  snapshots: [diaMas(HOY, -2), diaMas(HOY, -1), HOY],
  modo_local: true,
};

// ── Escribir ────────────────────────────────────────────────────────────────
const limpiar = (f) => { const { _prod, ...resto } = f; void _prod; return resto; };
const escribir = (nombre, datos) => {
  fs.writeFileSync(path.join(destino, nombre), JSON.stringify(datos, null, 1) + "\n", "utf8");
  console.log(`${nombre.padEnd(20)} ${JSON.stringify(datos).length.toLocaleString("es-MX").padStart(9)} bytes`);
};
escribir("estado.json", estado);
escribir("publicaciones.json", { generado_at: GENERADO, filas: publicaciones.map(limpiar) });
escribir("precios.json", { generado_at: GENERADO, parametros: OPT, filas: precios });
escribir("curvas.json", curvas);
escribir("historial.json", historial);
escribir("packing100.json", { generado_at: GENERADO, contenedor_mxn: CONTENEDOR_MXN, filas: packing });
escribir("contenedores.json", { filas: contenedores });
escribir("metricas.json", metricas);
escribir("parametros.json", parametros);
console.log(`publicaciones ${publicaciones.length} · precios ${precios.length} · packing ${packing.length} · contenedores ${contenedores.length} · palancas ${metricas.palancas.length}`);
