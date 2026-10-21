"use client";

// Monitor.tsx — La mesa de PRICING: el «Monitor de precios FULL» de José
// (artifact del 14-oct-2026) dentro del panel. Tarjetas por acción, filtros,
// «Reglas y umbrales» editables, la tabla (de lo que es a «Qué hacer») y el
// detalle de cada publicación con su gráfica y lo scrapeado.
//
// SOLO LECTURA: sugiere, no cambia ningún precio. Los umbrales se recuerdan en
// este navegador (localStorage), como en el artifact.

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight, EyeOff, Info, TrendingUp, Zap } from "lucide-react";

import type {
  DatosBitacoraML, PricingComp, PricingFoto, PricingHecho, PricingHechos, PricingMetricaML, PricingMotivos, PricingOrden, PricingPromoHoy, PricingPub,
  PricingSalud, PricingSerie, PricingVivo, PricingVivoPub,
} from "@/lib/api";
import { pricingMotivos, pricingSerie } from "@/lib/api";
import { pct1, pctS, pesos, pzs, reloj, type Reloj } from "./formato";
import EditorPrecio from "./EditorPrecio";
import Grafica from "./Grafica";
import { Mini } from "./Miniaturas";
import {
  ORDEN_ACC,
  REGLAS,
  SENTIDO,
  accion,
  calcular,
  metricas,
  descuentoPara,
  etiqueta,
  hoyDe,
  type ClaveAccion,
  type Fila,
  type Reglas,
  type Sentido,
} from "./reglas";
import Ofertas from "./Ofertas";
import Rivales from "./Rivales";
import Seguimiento from "./Seguimiento";
import { CLUSTERS, clusterizar, type Cluster } from "./clusters";

const LLAVE_REGLAS = "pricing-reglas-v1";
const COLOR: Record<Sentido, string> = { up: "var(--up)", down: "var(--down)", hold: "var(--hold)", none: "var(--muted)" };
const CAT_T: Record<string, [string, string]> = {
  correcta: ["correcta", "p-ok"],
  incorrecta: ["incorrecta", "p-bad"],
  revisar: ["revisar", "p-warn"],
};
const ES_SV = (k: ClaveAccion) => k.startsWith("sv_");

interface Filtros {
  ventas: "todas" | "con" | "sin";
  cat: "todas" | "correcta" | "incorrecta" | "revisar" | "editable";
  comp: "todas" | "con" | "sin";
  cuenta: string;
  accion: ClaveAccion | null;
  q: string;
  orden: string;
  /** «Hecho»: todas, las que sí se movieron en ML o las que no. */
  mov: "todas" | "si" | "no";
  /** «Se agota» (en vivo): días que dura el stock FULL al ritmo de hoy. */
  agota: "todas" | CatAgota;
  /** Low stock: 1 a LOW_STOCK piezas en FULL. */
  low: boolean;
  /** Promoción: sin ninguna que ponga precio, o que puede entrar a relámpago. */
  promo: "todas" | "sin" | "relampago";
  /** Visitas de ML en los 30 días de la foto (las mismas de «Conversión»). */
  vis: "todas" | "con" | "sin";
  /** Cluster de prioridad (clusters.ts): «Se va a agotar», «Mucha visita, poca venta», «Convierte, pero nadie la ve». */
  clu: "todas" | Cluster;
  /** Calidad de la publicación y experiencia de compra de ML (enrich.listing_health), por nivel. */
  cal: "todas" | NivelML;
  exp: "todas" | NivelML;
}

const F0: Filtros = {
  ventas: "todas", cat: "todas", comp: "todas", cuenta: "todas", accion: null, q: "", orden: "accion", mov: "todas",
  agota: "todas", low: false, promo: "todas", vis: "todas", clu: "todas", cal: "todas", exp: "todas",
};

/** Nivel normalizado de calidad o experiencia; «nd» = ML no lo calcula o el barrido aún no la mide. */
type NivelML = "bueno" | "medio" | "malo" | "nd";
const nivelML = (q?: PricingMetricaML | null): NivelML => q?.nivel ?? "nd";
const NIVELES_CAL: [NivelML, string, string][] = [
  ["bueno", "Profesional", "Calidad Profesional en ML"],
  ["medio", "Estándar", "Calidad Estándar en ML"],
  ["malo", "Básica", "Calidad Básica en ML: tiene mejoras pendientes"],
  ["nd", "Sin dato", "ML no la calcula o el barrido de calidad aún no la mide"],
];
const NIVELES_EXP: [NivelML, string, string][] = [
  ["bueno", "Buena", "Experiencia de compra buena"],
  ["medio", "Media", "Experiencia de compra media"],
  ["malo", "Mala", "Experiencia de compra mala: reclamos, cancelaciones o demoras"],
  ["nd", "Sin dato", "ML no tiene datos de experiencia o el barrido aún no la mide"],
];
function cuentaNivel(fs: FilaV[], k: "cal" | "exp"): Record<NivelML, number> {
  const n: Record<NivelML, number> = { bueno: 0, medio: 0, malo: 0, nd: 0 };
  fs.forEach((f) => { n[f[k]] += 1; });
  return n;
}

// ── SE AGOTA y LOW STOCK (Brandon, 16-oct-2026) ──────────────────────────────
// Con el stock de ML y las ventas de kubera de AHORA (/api/pricing/vivo); si
// no llegaron, con la foto. Ritmo como en reglas.ts: 14 días si vendió 3 o
// más piezas, si no 30. Categorías: 1 a 7 días, más de 7 a 30, más de 30
// (incluye las que no venden), y agotadas (0 en FULL).
type CatAgota = "c1" | "c2" | "c3" | "cero";
const LOW_STOCK = 7;
const CAT_AGOTA: [CatAgota, string, string][] = [
  ["c1", "1–7 días", "Se acaban en 7 días o menos al ritmo de hoy"],
  ["c2", "7–30 días", "Se acaban en más de 7 y hasta 30 días"],
  ["c3", "Más de 30 días", "Tardan más de 30 días, o no venden"],
  ["cero", "Agotadas", "0 piezas en FULL ahora"],
];

interface Agota {
  stock: number;
  vel: number;
  dias: number;
  cat: CatAgota;
  /** true = stock leído de ML ahora; false = el de la foto. */
  vivo: boolean;
  /** Piezas de 30 días de ahora (kubera); null = sin lectura en vivo. */
  u30: number | null;
  /** Visitas de 30 días (ML, cada 30 min) y conversión = piezas ÷ visitas. */
  vis30: number;
  visVivo: boolean;
  conv: number | null;
}
type FilaV = Fila & { v: Agota; clu: Cluster | null; cal: NivelML; exp: NivelML };

function agotaDe(f: Fila, V: Map<string, PricingVivoPub> | null, visitas?: Record<string, number> | null): Agota {
  const x = V?.get(f.p.id);
  const enVivo = x != null && x.stock != null;
  const stock = enVivo ? (x!.stock as number) : f.p.stock;
  const vel = x && x.u14 != null && x.u30 != null ? (x.u14 >= 3 ? x.u14 / 14 : x.u30 / 30) : f.m.vel;
  const dias = stock > 0 ? (vel > 0 ? stock / vel : Infinity) : 0;
  const cat: CatAgota = !(stock > 0) ? "cero" : dias <= 7 ? "c1" : dias <= 30 ? "c2" : "c3";
  const u30 = x && x.u30 != null ? x.u30 : null;
  const visV = visitas?.[f.p.id];
  const vis30 = visV != null ? visV : f.m.vis30;
  const piezas = u30 ?? f.m.u30;
  return { stock, vel, dias, cat, vivo: enVivo, u30, vis30, visVivo: visV != null, conv: vis30 > 0 ? piezas / vis30 : null };
}

/** Lo que llega de /api/pricing/hechos, ya por id de publicación. */
interface Extra {
  estado: "cargando" | "listo" | "error";
  hecho: Map<string, PricingHecho>;
  promo: Record<string, PricingPromoHoy | null>;
  promosLeido: string | null;
  kubera: boolean;
}

const editable = (f: Fila) => f.p.cat.v === "incorrecta" && !((f.p.sold ?? 0) > 0);

function pasa(f: FilaV, F: Filtros, sinAccion = false, X?: Extra, sinAgota = false): boolean {
  const { p, m, a } = f;
  if (!sinAgota) {
    if (F.agota !== "todas" && f.v.cat !== F.agota) return false;
    if (F.low && !(f.v.stock > 0 && f.v.stock <= LOW_STOCK)) return false;
  }
  if (F.promo !== "todas" && X?.estado === "listo") {
    const pr = X.promo[p.id];
    if (!pr) return false;
    if (F.promo === "sin" && pr.por) return false;
    if (F.promo === "relampago" && !pr.relampago) return false;
  }
  if (F.clu !== "todas" && f.clu !== F.clu) return false;
  if (F.cal !== "todas" && f.cal !== F.cal) return false;
  if (F.exp !== "todas" && f.exp !== F.exp) return false;
  if (F.mov !== "todas" && X?.estado === "listo" && X.hecho.has(p.id) !== (F.mov === "si")) return false;
  if (F.ventas === "con" && !(m.u30 > 0)) return false;
  if (F.ventas === "sin" && m.u30 > 0) return false;
  if (F.vis === "con" && !(f.v.vis30 > 0)) return false;
  if (F.vis === "sin" && f.v.vis30 > 0) return false;
  if (F.cat === "editable" ? !editable(f) : F.cat !== "todas" && p.cat.v !== F.cat) return false;
  if (F.comp === "con" && m.ref == null) return false;
  if (F.comp === "sin" && m.ref != null) return false;
  if (F.cuenta !== "todas" && p.c !== F.cuenta) return false;
  if (!sinAccion && F.accion && a.k !== F.accion) return false;
  if (F.q.trim()) {
    const q = F.q.trim().toLowerCase();
    if (!((p.sku || "") + " " + (p.t || "") + " " + p.id).toLowerCase().includes(q)) return false;
  }
  return true;
}

// Por encabezado: llave → valor (los que no tienen dato van al final en las dos direcciones).
const VALOR: Record<string, (f: FilaV) => number | null> = {
  precio: (f) => f.m.precio,
  comp: (f) => f.m.ref,
  dif: (f) => f.m.dif,
  stock: (f) => f.v.stock,
  ventas: (f) => f.m.u30,
  conv: (f) => f.v.conv,
  dias: (f) => (isFinite(f.v.dias) ? f.v.dias : null),
  v7: (f) => f.x.sug ?? f.x.hoy,
  ganan: (f) => (f.x.sug ?? f.x.hoy) - f.x.hoy,
};
const porValor = (k: string, dir: number) => (x: FilaV, y: FilaV) => {
  const a = VALOR[k](x);
  const b = VALOR[k](y);
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  return dir * (a - b);
};
const ORDENES: Record<string, (x: FilaV, y: FilaV) => number> = {
  accion: (x, y) => ORDEN_ACC.indexOf(x.a.k) - ORDEN_ACC.indexOf(y.a.k) || y.v.stock - x.v.stock,
  precio_desc: porValor("precio", -1),
  precio_asc: porValor("precio", 1),
  v7: porValor("v7", -1),
  ganan: porValor("ganan", -1),
  stock: (x, y) => y.v.stock - x.v.stock,
  dias: (x, y) => x.v.dias - y.v.dias || y.v.stock - x.v.stock,
  arriba: (x, y) => (y.m.dif ?? -9) - (x.m.dif ?? -9),
  abajo: (x, y) => (x.m.dif ?? 9) - (y.m.dif ?? 9),
  ventas: (x, y) => y.m.u30 - x.m.u30,
  conv: (x, y) => (y.v.conv ?? -1) - (x.v.conv ?? -1),
};

/** Motivo corto (el completo va en el tooltip y en el detalle). */
function sinRef(c: PricingComp): string {
  if (c.adultos) return "No medible · adultos";
  if (c.busqueda_mala) return "Búsqueda equivocada";
  if (c.visual && (c.visual.no || c.visual.no_se)) return "Sin comparables (fotos)";
  if (/sin SKU/i.test(c.motivo || "")) return "Sin SKU";
  if (/sin búsqueda/i.test(c.motivo || "")) return "Sin búsqueda";
  return "Sin comparables";
}

function leerReglas(): Reglas {
  try {
    const guardadas = JSON.parse(window.localStorage.getItem(LLAVE_REGLAS) || "{}");
    return { ...REGLAS, ...(guardadas && typeof guardadas === "object" ? guardadas : {}) };
  } catch {
    return { ...REGLAS };
  }
}

function guardarReglas(R: Reglas) {
  try {
    window.localStorage.setItem(LLAVE_REGLAS, JSON.stringify(R));
  } catch {
    // ventana privada o almacenamiento bloqueado: las reglas duran lo que la página
  }
}

export default function Monitor({
  foto,
  hechos,
  vivo,
  salud,
  esAdmin = false,
}: {
  foto: PricingFoto;
  hechos?: PricingHechos | null | "error";
  vivo?: PricingVivo | null | "error";
  salud?: PricingSalud | null | "error";
  /** Cambiar precios en ML desde la celda «Precio hoy» (v0.665.0). */
  esAdmin?: boolean;
}) {
  const [R, setR] = useState<Reglas>(REGLAS);
  const [F, setF] = useState<Filtros>(F0);
  const [abiertos, setAbiertos] = useState<Set<number>>(() => new Set());
  const [rivSku, setRivSku] = useState<string | null>(null);
  // «Ofertas de ML» (v0.672.0): se monta al abrirla por primera vez; lee ML solo entonces.
  const [verOfertas, setVerOfertas] = useState(false);
  const [ofertasUsadas, setOfertasUsadas] = useState(false);
  // «Seguimiento semanal» (v0.678.0): igual, se monta al abrirlo por primera vez.
  const [verSeguimiento, setVerSeguimiento] = useState(false);
  const [seguimientoUsado, setSeguimientoUsado] = useState(false);
  const titulos = useMemo(() => new Map(foto.pubs.map((p) => [p.id, p.t])), [foto.pubs]);

  useEffect(() => setR(leerReglas()), []);
  const cambiarReglas = (parcial: Partial<Reglas> | null) => {
    setR((prev) => {
      const nuevo = parcial ? { ...prev, ...parcial } : { ...REGLAS };
      guardarReglas(nuevo);
      return nuevo;
    });
  };

  const rel = useMemo(() => reloj(foto.meta.d0), [foto.meta.d0]);
  const X = useMemo<Extra>(() => {
    if (!hechos || hechos === "error")
      return { estado: hechos === "error" ? "error" : "cargando", hecho: new Map(), promo: {}, promosLeido: null, kubera: false };
    return {
      estado: "listo",
      hecho: new Map(hechos.hechos.map((h) => [h.id, h])),
      promo: hechos.promos || {},
      promosLeido: hechos.promos_leido,
      kubera: hechos.kubera,
    };
  }, [hechos]);
  // Para el acceso a «Ofertas de ML»: cuántas de TODAS las publicaciones pueden entrar hoy a una relámpago.
  const relampagoTotal = useMemo(
    () => (X.estado === "listo" ? foto.pubs.filter((p) => X.promo[p.id]?.relampago).length : 0),
    [X, foto.pubs],
  );
  // PRECIO DE AHORA (v0.662.0): la foto es del 14-oct y los precios cambian (campañas, descuentos). Si hay
  // lectura de promociones de ML (cada 30 min), el precio que se cobra y el normal salen de ahí, y con ellos
  // todo lo que se calcula: «Precio hoy», contra la competencia, venta en 7 días y «Qué hacer» del detalle.
  const preciosVivo = vivo && vivo !== "error" ? vivo.precios ?? null : null;
  const pubs = useMemo(
    () =>
      foto.pubs.map((p) => {
        const pv = preciosVivo?.[p.id];
        const pr = X.promo[p.id];
        const cobra = pv?.cobra ?? pr?.cobra ?? null;
        if (cobra == null) return p;
        const n0 = pv ? pv.normal : pr?.normal;
        const normal = n0 && n0 > cobra ? n0 : cobra;
        return { ...p, p: { ...p.p, a: cobra, n: normal } };
      }),
    [foto.pubs, X.promo, preciosVivo],
  );
  const { filas, convTipica } = useMemo(() => calcular(pubs, R), [pubs, R]);
  const V = useMemo(
    () => (vivo && vivo !== "error" ? new Map(Object.entries(vivo.pubs)) : null),
    [vivo],
  );
  const visitasVivo = vivo && vivo !== "error" ? vivo.visitas ?? null : null;
  const pubsSalud = salud && salud !== "error" && salud.kubera ? salud.pubs : null;
  const filasV = useMemo<FilaV[]>(() => {
    const xs = filas.map((f) => ({ ...f, v: agotaDe(f, V, visitasVivo) }));
    // Los clusters se calculan sobre TODAS las publicaciones (la clase ABC es del catálogo, no de la vista).
    const clu = clusterizar(xs.map((f) => ({
      stock: f.v.stock, dias: f.v.dias, vis30: f.v.vis30, conv: f.v.conv,
      venta30: (f.v.u30 ?? f.m.u30) * f.m.precio,
    })));
    return xs.map((f, i) => ({
      ...f, clu: clu[i],
      cal: nivelML(pubsSalud?.[f.p.id]?.c), exp: nivelML(pubsSalud?.[f.p.id]?.e),
    }));
  }, [filas, V, visitasVivo, pubsSalud]);

  // Lo que se movió desde la lectura anterior (venta o resurtido): la fila se marca 3 minutos.
  const anterior = useRef<Map<string, PricingVivoPub> | null>(null);
  const [movidos, setMovidos] = useState<Map<string, number>>(() => new Map());
  useEffect(() => {
    if (!V) return;
    const prev = anterior.current;
    if (prev) {
      const ahora = Date.now();
      const nuevos = new Map(movidos);
      V.forEach((x, id) => {
        const y = prev.get(id);
        if (y && (y.stock !== x.stock || y.u30 !== x.u30)) nuevos.set(id, ahora);
      });
      nuevos.forEach((t, id) => {
        if (ahora - t > 180_000) nuevos.delete(id);
      });
      setMovidos(nuevos);
    }
    anterior.current = V;
  }, [V]); // eslint-disable-line react-hooks/exhaustive-deps
  const ultimas = vivo && vivo !== "error" ? vivo.ultimas ?? [] : [];
  const skuDe = useMemo(() => new Map(foto.pubs.map((p) => [p.id, p.sku])), [foto.pubs]);
  const series = { pubs: foto.meta.img.pubs || "p", riv: foto.meta.img.riv || "s" };

  const base = useMemo(() => filasV.filter((f) => pasa(f, F, true, X)), [filasV, F, X]);
  // Conteos de «Se agota»: con los demás filtros, sin el suyo.
  const baseAgota = useMemo(() => filasV.filter((f) => pasa(f, F, false, X, true)), [filasV, F, X]);
  // Conteos de «Promoción»: con los demás filtros, sin el suyo.
  const nPromo = useMemo(() => {
    const n = { sin: 0, relampago: 0 };
    if (X.estado !== "listo") return n;
    filasV.filter((f) => pasa(f, { ...F, promo: "todas" }, false, X)).forEach((f) => {
      const pr = X.promo[f.p.id];
      if (pr && !pr.por) n.sin += 1;
      if (pr?.relampago) n.relampago += 1;
    });
    return n;
  }, [filasV, F, X]);
  // Conteos de «Visitas 30 d»: con los demás filtros, sin el suyo.
  const nVis = useMemo(() => {
    const n = { con: 0, sin: 0 };
    filasV.filter((f) => pasa(f, { ...F, vis: "todas" }, false, X)).forEach((f) => {
      if (f.v.vis30 > 0) n.con += 1;
      else n.sin += 1;
    });
    return n;
  }, [filasV, F, X]);
  // Conteos de «Calidad ML» y «Experiencia ML»: cada uno con los demás filtros, sin el suyo.
  const nCal = useMemo(() => cuentaNivel(filasV.filter((f) => pasa(f, { ...F, cal: "todas" }, false, X)), "cal"), [filasV, F, X]);
  const nExp = useMemo(() => cuentaNivel(filasV.filter((f) => pasa(f, { ...F, exp: "todas" }, false, X)), "exp"), [filasV, F, X]);
  // Conteos de «Prioridad»: con los demás filtros, sin el suyo.
  const nClu = useMemo(() => {
    const n: Record<Cluster, number> = { agota: 0, fuga: 0, joya: 0 };
    filasV.filter((f) => pasa(f, { ...F, clu: "todas" }, false, X)).forEach((f) => {
      if (f.clu) n[f.clu] += 1;
    });
    return n;
  }, [filasV, F, X]);
  const nAgota = useMemo(() => {
    const n: Record<string, number> = { c1: 0, c2: 0, c3: 0, cero: 0, low: 0 };
    baseAgota.forEach((f) => {
      n[f.v.cat] += 1;
      if (f.v.stock > 0 && f.v.stock <= LOW_STOCK) n.low += 1;
    });
    return n;
  }, [baseAgota]);
  const vista = useMemo(() => {
    const ord = F.orden.startsWith("h:")
      ? porValor(F.orden.split(":")[1], +F.orden.split(":")[2])
      : ORDENES[F.orden] ?? ORDENES.accion;
    return filasV.filter((f) => pasa(f, F, false, X)).sort(ord);
  }, [filasV, F, X]);
  // Valor en FULL de la vista: piezas de ahora × lo que se cobra (la promoción leída de ML; si no, la foto).
  const valorFull = vista.reduce((s, f) => s + f.v.stock * (X.promo[f.p.id]?.cobra ?? f.m.precio), 0);
  const piezasFull = vista.reduce((s, f) => s + f.v.stock, 0);
  const movidas = useMemo(() => vista.filter((f) => X.hecho.has(f.p.id)), [vista, X]);
  const pzsDespues = movidas.reduce((s, f) => s + (X.hecho.get(f.p.id)?.despues?.pzs ?? 0), 0);

  const conteo = useMemo(() => {
    const n: Partial<Record<ClaveAccion, number>> = {};
    base.forEach((f) => {
      n[f.a.k] = (n[f.a.k] || 0) + 1;
    });
    return n;
  }, [base]);

  let uh = 0, ph = 0, us = 0, ps = 0;
  vista.forEach((f) => {
    const x = f.x;
    const s2 = x.sug ?? x.hoy;
    const p2 = x.pSug ?? x.pHoy;
    uh += x.hoy;
    ph += x.hoy * x.pHoy;
    us += s2;
    ps += s2 * p2;
  });
  const difTot = ph ? ps / ph - 1 : 0;

  const alternar = (i: number) =>
    setAbiertos((prev) => {
      const s = new Set(prev);
      if (s.has(i)) s.delete(i);
      else s.add(i);
      return s;
    });
  const ordenarPor = (k: string) =>
    setF((prev) => ({ ...prev, orden: prev.orden === `h:${k}:-1` ? `h:${k}:1` : `h:${k}:-1` }));
  const ariaSort = (k: string): "ascending" | "descending" | "none" =>
    F.orden === `h:${k}:1` ? "ascending" : F.orden === `h:${k}:-1` ? "descending" : "none";

  const fuentes: Record<string, number> = {};
  foto.pubs.forEach((p) => {
    const fu = p.comp?.fuente;
    if (fu) fuentes[fu] = (fuentes[fu] || 0) + 1;
  });
  const fuente = Object.entries(fuentes).sort((a, b) => b[1] - a[1])[0]?.[0];
  const hoyLargo = new Date(foto.meta.hoy + "T12:00:00").toLocaleDateString("es-MX", { day: "numeric", month: "long", year: "numeric" });

  const seg = <K extends "ventas" | "cat" | "comp" | "cuenta" | "mov">(campo: K, opciones: [Filtros[K], string][]) => (
    <div className="seg">
      {opciones.map(([v, t]) => (
        <button key={String(v)} type="button" aria-pressed={F[campo] === v} onClick={() => setF((prev) => ({ ...prev, [campo]: v }))}>
          {t}
        </button>
      ))}
    </div>
  );

  return (
    <div className="envoltura">
      <header className="cabecera">
        <div>
          <h1>Pricing · Monitor de precios FULL</h1>
          <p className="sub">
            {foto.pubs.length} publicaciones FULL de BEKURA y SANCORFASHION · en vivo: stock y ventas al minuto, precio de
            venta cada 5 min, promociones y visitas cada 30 min
            {fuente ? ` · competencia: ${fuente}` : ""}
          </p>
        </div>
        <div className="sello">
          <span className="badge">
            <EyeOff size={13} aria-hidden /> Oculta · admin e invitados
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Info size={14} aria-hidden />{" "}
            {esAdmin
              ? "«Cambiar precio» y «Ofertas de ML» escriben en ML (con bitácora); «Hecho» muestra lo ya movido"
              : "Solo lectura: esta pantalla no cambia ningún precio; «Hecho» muestra lo ya movido en ML"}
          </span>
          <span title={`Lista de publicaciones y competencia: revisión del ${hoyLargo}`}>
            {vivo && vivo !== "error" ? (
              <>
                <span className="pulso" aria-hidden /> En vivo · stock {horaCorta(vivo.leido)}
                {vivo.precios_leido ? ` · precios ${horaCorta(vivo.precios_leido)}` : " · precios …"}
                {X.promosLeido ? ` · promos ${horaCorta(X.promosLeido)}` : ""}
              </>
            ) : vivo === "error" ? (
              "Sin lectura en vivo"
            ) : (
              "Leyendo en vivo…"
            )}
          </span>
        </div>
      </header>

      {/* Las dos herramientas, a la vista (v0.679.0, Brandon: el botón de ofertas era chico y no se distinguía). */}
      <section className="atajos" aria-label="Herramientas">
        <button
          type="button"
          className="atajo"
          onClick={() => {
            setOfertasUsadas(true);
            setVerOfertas(true);
          }}
        >
          <span className="ico" aria-hidden>
            <Zap size={22} />
          </span>
          <span className="txt">
            <b>Ofertas de Mercado Libre</b>
            <span>Relámpago, Buen Fin y campaña propia: qué se puede tomar y a qué precio quedaría</span>
          </span>
          {relampagoTotal > 0 && (
            <span className="num" title="Publicaciones que pueden entrar hoy a una relámpago">
              {relampagoTotal}
              <small>pueden entrar a relámpago</small>
            </span>
          )}
          <ChevronRight size={20} aria-hidden className="flecha" />
        </button>
        <button
          type="button"
          className="atajo"
          onClick={() => {
            setSeguimientoUsado(true);
            setVerSeguimiento(true);
          }}
        >
          <span className="ico" aria-hidden>
            <TrendingUp size={22} />
          </span>
          <span className="txt">
            <b>Seguimiento semanal</b>
            <span>¿Se movieron? Ventas y visitas, semana por semana, de lo que se cambió</span>
          </span>
          {X.hecho.size > 0 && (
            <span className="num" title="Publicaciones con precio movido en ML">
              {X.hecho.size}
              <small>con precio movido</small>
            </span>
          )}
          <ChevronRight size={20} aria-hidden className="flecha" />
        </button>
      </section>

      <section className="resumen" aria-label="Acciones sugeridas">
        {ORDEN_ACC.filter((k) => conteo[k]).map((k) => (
          <button
            key={k}
            type="button"
            className={`chip-acc${ES_SV(k) ? " sv" : ""}`}
            aria-pressed={F.accion === k}
            onClick={() => setF((prev) => ({ ...prev, accion: k }))}
          >
            <span className="dot" style={{ background: COLOR[SENTIDO[k]] }} />
            <span>{etiqueta(k, R)}</span>
            <b>{conteo[k]}</b>
          </button>
        ))}
        <button type="button" className="chip-acc" aria-pressed={!F.accion} onClick={() => setF((prev) => ({ ...prev, accion: null }))}>
          <span>Todas</span>
          <b>{base.length}</b>
        </button>
      </section>

      <section className="filtros" aria-label="Filtros">
        <div className="grupo" title="Clusters de prioridad, con stock FULL, visitas y ventas de 30 días en vivo">
          <span>Prioridad</span>
          <div className="seg">
            <button type="button" aria-pressed={F.clu === "todas"} onClick={() => setF((prev) => ({ ...prev, clu: "todas" }))}>
              Todas
            </button>
            {CLUSTERS.map(([k, t, tip]) => (
              <button key={k} type="button" title={tip} aria-pressed={F.clu === k} onClick={() => setF((prev) => ({ ...prev, clu: k }))}>
                {t} <b className="cnt">{nClu[k]}</b>
              </button>
            ))}
          </div>
        </div>
        <div className="grupo">
          <span>Ventas 30 d</span>
          {seg("ventas", [["todas", "Todas"], ["con", "Con ventas"], ["sin", "Sin ventas"]])}
        </div>
        <div className="grupo" title="Visitas de ML de los últimos 30 días (se releen cada 30 min)">
          <span>Visitas 30 d</span>
          <div className="seg">
            <button type="button" aria-pressed={F.vis === "todas"} onClick={() => setF((prev) => ({ ...prev, vis: "todas" }))}>
              Todas
            </button>
            <button type="button" aria-pressed={F.vis === "con"} onClick={() => setF((prev) => ({ ...prev, vis: "con" }))}>
              Con visitas <b className="cnt">{nVis.con}</b>
            </button>
            <button type="button" title="Ninguna visita en 30 días" aria-pressed={F.vis === "sin"}
              onClick={() => setF((prev) => ({ ...prev, vis: "sin" }))}>
              Sin visitas <b className="cnt">{nVis.sin}</b>
            </button>
          </div>
        </div>
        {([["cal", "Calidad ML", NIVELES_CAL, nCal], ["exp", "Experiencia ML", NIVELES_EXP, nExp]] as const).map(([campo, titulo, niveles, n]) => (
          <div key={campo} className="grupo"
            title={pubsSalud ? `Lo que guarda el barrido de calidad de ML (leído ${fechaCorta((salud as PricingSalud).leido)})` : salud === "error" || (salud && !salud.kubera) ? "No se pudo leer /api/pricing/salud" : "Cargando la calidad de ML…"}>
            <span>{titulo}{pubsSalud ? "" : salud == null ? " …" : " · sin lectura"}</span>
            <div className="seg">
              <button type="button" aria-pressed={F[campo] === "todas"} onClick={() => setF((prev) => ({ ...prev, [campo]: "todas" }))}>
                Todas
              </button>
              {niveles.map(([k, t, tip]) => (
                <button key={k} type="button" title={tip} disabled={!pubsSalud} aria-pressed={F[campo] === k}
                  onClick={() => setF((prev) => ({ ...prev, [campo]: k }))}>
                  {t} {pubsSalud ? <b className="cnt">{n[k]}</b> : null}
                </button>
              ))}
            </div>
          </div>
        ))}
        <div className="grupo">
          <span>Categoría</span>
          {seg("cat", [["todas", "Todas"], ["correcta", "Correctas"], ["incorrecta", "Incorrectas"], ["revisar", "Revisar"], ["editable", "Editables"]])}
        </div>
        <div className="grupo">
          <span>Competencia</span>
          {seg("comp", [["todas", "Todas"], ["con", "Comparables"], ["sin", "Sin referencia"]])}
        </div>
        <div className="grupo">
          <span>Cuenta</span>
          {seg("cuenta", [["todas", "Ambas"], ["BEKURA", "BEKURA"], ["SANCORFASHION", "SANCORFASHION"]])}
        </div>
        <div className="grupo" title={vivo && vivo !== "error" ? `Stock de ML y ventas de kubera leídos ${fechaCorta(vivo.leido)}` : "No llegó la lectura en vivo"}>
          <span>
            Se agota {vivo && vivo !== "error" ? <>· <span className="pulso" aria-hidden /> en vivo {horaCorta(vivo.leido)}</> : vivo === "error" ? "· sin lectura en vivo" : "…"}
          </span>
          <div className="seg">
            <button type="button" aria-pressed={F.agota === "todas"} onClick={() => setF((prev) => ({ ...prev, agota: "todas" }))}>
              Todas
            </button>
            {CAT_AGOTA.map(([k, t, tip]) => (
              <button key={k} type="button" title={tip} aria-pressed={F.agota === k} onClick={() => setF((prev) => ({ ...prev, agota: k }))}>
                {t} <b className="cnt">{nAgota[k]}</b>
              </button>
            ))}
          </div>
          <div className="seg">
            <button
              type="button"
              title={`Publicaciones con 1 a ${LOW_STOCK} piezas en FULL`}
              aria-pressed={F.low}
              onClick={() => setF((prev) => ({ ...prev, low: !prev.low }))}
            >
              Low stock ≤ {LOW_STOCK} pzs <b className="cnt">{nAgota.low}</b>
            </button>
          </div>
        </div>
        <div className="grupo" title={X.promosLeido ? `Promociones leídas de ML ${fechaCorta(X.promosLeido)}` : ""}>
          <span>Promoción</span>
          <div className="seg">
            <button type="button" aria-pressed={F.promo === "todas"} onClick={() => setF((prev) => ({ ...prev, promo: "todas" }))}>
              Todas
            </button>
            <button type="button" title="Ninguna promoción pone su precio hoy" aria-pressed={F.promo === "sin"}
              onClick={() => setF((prev) => ({ ...prev, promo: "sin" }))}>
              Sin promoción <b className="cnt">{nPromo.sin}</b>
            </button>
            <button type="button" title="ML la deja entrar a una oferta relámpago (normal o co-fondeada)" aria-pressed={F.promo === "relampago"}
              onClick={() => setF((prev) => ({ ...prev, promo: "relampago" }))}>
              Puede entrar a relámpago <b className="cnt">{nPromo.relampago}</b>
            </button>
          </div>
        </div>
        <div className="grupo">
          <span>Hecho</span>
          {seg("mov", [["todas", "Todas"], ["si", "Precio movido"], ["no", "Sin movimiento"]])}
        </div>
        <div className="grupo">
          <label htmlFor="pm-q" className="sm">
            Buscar
          </label>
          <input
            type="search"
            id="pm-q"
            placeholder="SKU, título o MLM…"
            value={F.q}
            onChange={(e) => setF((prev) => ({ ...prev, q: e.target.value }))}
          />
        </div>
        <div className="grupo">
          <label htmlFor="pm-orden" className="sm">
            Ordenar
          </label>
          <select id="pm-orden" value={F.orden.startsWith("h:") ? "" : F.orden} onChange={(e) => setF((prev) => ({ ...prev, orden: e.target.value }))}>
            <option value="" hidden>
              Por columna
            </option>
            <option value="accion">Por acción</option>
            <option value="stock">Más stock FULL</option>
            <option value="dias">Se agota antes</option>
            <option value="arriba">Más arriba de la competencia</option>
            <option value="abajo">Más abajo de la competencia</option>
            <option value="ventas">Más ventas 30 d</option>
            <option value="conv">Mayor conversión</option>
            <option value="precio_desc">Precio: mayor a menor</option>
            <option value="precio_asc">Precio: menor a mayor</option>
            <option value="v7">Más venta en 7 d con lo sugerido</option>
            <option value="ganan">Más piezas ganadas en 7 d</option>
          </select>
        </div>
      </section>

      <PanelReglas R={R} cambiar={cambiarReglas} />

      <p className="total">
        Próximos 7 días de las {vista.length} publicaciones a la vista: al precio de hoy{" "}
        <b>
          {pzs(uh)} pzs · {pesos(ph)}
        </b>
        ; con lo sugerido{" "}
        <b>
          {pzs(us)} pzs · {pesos(ps)}
        </b>{" "}
        ({difTot >= 0 ? "+" : ""}
        {Math.round(difTot * 100)} % en pesos).
        {" "}
        En FULL{vivo && vivo !== "error" ? " ahora" : ""}:{" "}
        <b>
          {pzs(piezasFull)} pzs · {pesos(valorFull)}
        </b>
        .
        {X.estado === "listo" && (
          <>
            {" "}
            Precio movido en ML: <b>{movidas.length}</b> de ellas
            {X.kubera ? (
              <>
                , con <b>{pzsDespues} pzs</b> vendidas desde el cambio
              </>
            ) : (
              " (no se pudo leer lo vendido después)"
            )}
            .
          </>
        )}
      </p>

      {ultimas.length > 0 && (
        <section className="ultimas" aria-label="Últimas ventas de Mercado Libre">
          <span className="lbl">Últimas ventas</span>
          {ultimas.slice(0, 10).map((u, k) => (
            <span key={k} className={`venta${u.estado === "cancelled" ? " cancelada" : ""}${movidos.has(u.id) ? " nueva" : ""}`}
              title={`${u.cuenta} · ${u.id}${u.estado === "cancelled" ? " · cancelada" : ""}`}>
              <b>{u.sku || skuDe.get(u.id) || u.id}</b> ×{u.pzs} · {haceCuanto(u.cuando)}
            </span>
          ))}
        </section>
      )}

      <div className="tabla-wrap">
        <table>
          <thead>
            <tr>
              <th>Publicación</th>
              <th className="n orden" aria-sort={ariaSort("precio")} onClick={() => ordenarPor("precio")}>
                Precio hoy
              </th>
              <th title="La promoción que pone el precio que se cobra hoy en ML, más cupones, «lleva más» y lo programado">
                Promoción hoy
              </th>
              <th
                className="n orden"
                aria-sort={ariaSort("comp")}
                onClick={() => ordenarPor("comp")}
                title="Media (mediana), mínimo y máximo de los rivales que cuentan. Toca la media de una fila para ver lo scrapeado"
              >
                Competencia
              </th>
              <th className="n orden" aria-sort={ariaSort("stock")} onClick={() => ordenarPor("stock")} title="Piezas en FULL: en vivo de ML si llegó la lectura">
                Stock
              </th>
              <th className="n orden" aria-sort={ariaSort("ventas")} onClick={() => ordenarPor("ventas")}>
                Ventas 30 d
              </th>
              <th className="n orden" aria-sort={ariaSort("conv")} onClick={() => ordenarPor("conv")}>
                Conversión
              </th>
              <th
                className="salud"
                title="Calidad de la publicación (puntaje de ML) y experiencia de compra, como las mide el barrido de calidad cada hora"
              >
                Calidad · Experiencia
              </th>
              <th className="n orden" aria-sort={ariaSort("dias")} onClick={() => ordenarPor("dias")} title="Días que dura el stock al ritmo de hoy, contados desde hoy">
                Se agota
              </th>
              <th
                className="n orden"
                aria-sort={ariaSort("v7")}
                onClick={() => ordenarPor("v7")}
                title="Piezas y pesos en 7 días: al precio de hoy y al sugerido"
              >
                Venta 7 d
              </th>
              <th>Categoría</th>
              <th className="hecho" title="Lo que ya se movió en ML: precio antes → precio nuevo, y lo vendido desde el cambio">
                Hecho
              </th>
            </tr>
          </thead>
          <tbody>
            {vista.length === 0 ? (
              <tr>
                <td colSpan={12} className="vacio">
                  Ninguna publicación cumple estos filtros.
                </td>
              </tr>
            ) : (
              vista.map((f) => (
                <Fragment key={f.p.id}>
                  <FilaPub f={f} R={R} rel={rel} abierta={abiertos.has(f.i)} tieneRiv={!!(f.p.sku && foto.riv[f.p.sku])}
                    serie={series.pubs} onAlternar={() => alternar(f.i)} onRivales={setRivSku} X={X} movido={movidos.has(f.p.id)}
                    salud={salud} esAdmin={esAdmin} fotoGen={foto.meta.generado} />
                  {abiertos.has(f.i) && (
                    <tr className="det">
                      <td colSpan={12}>
                        <Detalle f={f} R={R} rel={rel} nRiv={f.p.sku ? foto.riv[f.p.sku]?.r.length ?? 0 : 0} onRivales={setRivSku} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))
            )}
          </tbody>
        </table>
      </div>

      <Notas R={R} convTipica={convTipica} fallas={foto.meta.fallas?.length ?? 0} />

      <Rivales sku={rivSku} filas={filas} riv={foto.riv} series={series} onCerrar={() => setRivSku(null)} />
      {ofertasUsadas && <Ofertas abierto={verOfertas} onCerrar={() => setVerOfertas(false)} esAdmin={esAdmin} />}
      {seguimientoUsado && <Seguimiento abierto={verSeguimiento} onCerrar={() => setVerSeguimiento(false)} titulos={titulos} />}
    </div>
  );
}

/**
 * Lo que la pantalla veía de la fila al cambiar el precio (v0.673.0): va a la bitácora del esquema `pricing`
 * (accion_publicacion.contexto, PLANO: solo números, textos o null) junto con la recomendación de «Qué hacer»,
 * los umbrales con que se calculó y de qué foto salían los datos. Sin eso, la recomendación no se puede reproducir.
 */
function bitacoraDe(f: FilaV, R: Reglas, foto?: string): DatosBitacoraML {
  const n = (x: number | null | undefined) => (x == null || !Number.isFinite(x) ? null : Math.round(x * 10000) / 10000);
  const { m, a, v, p } = f;
  return {
    contexto: {
      cobra_hoy: n(m.precio),
      normal: n(m.normal),
      descuento: n(m.desc),
      ventas_7d: n(m.u7),
      ventas_14d: n(m.u14),
      ventas_30d: n(v.u30 ?? m.u30),
      visitas_30d: n(v.vis30),
      conversion: n(v.conv),
      stock_full: n(v.stock),
      dias_agota: n(v.dias),
      comp_referencia: n(m.ref),
      dif_competencia: n(m.dif),
      posicion: m.pos ?? null,
      prioridad: f.clu ?? null,
      calidad_ml: f.cal,
      experiencia_ml: f.exp,
      categoria: p.cat.v ?? null,
      que_hacer: a.t || null,
      por_que: a.por || null,
    },
    recomendacion: a.k,
    precio_recomendado: a.precio ?? null,
    umbrales: { ...R },
    foto: foto ?? null,
  };
}

function FilaPub({
  f,
  R,
  rel,
  abierta,
  tieneRiv,
  serie,
  onAlternar,
  onRivales,
  X,
  movido,
  salud,
  esAdmin = false,
  fotoGen,
}: {
  X: Extra;
  movido?: boolean;
  salud?: PricingSalud | null | "error";
  esAdmin?: boolean;
  /** De qué foto salen los datos de la fila (va a la bitácora de pricing con cada cambio). */
  fotoGen?: string;
  f: FilaV;
  R: Reglas;
  rel: Reloj;
  abierta: boolean;
  tieneRiv: boolean;
  serie: string;
  onAlternar: () => void;
  onRivales: (sku: string) => void;
}) {
  const { p, m } = f;
  const c = p.comp || {};
  const cat = CAT_T[p.cat.v] ?? CAT_T.revisar;
  const H = hoyDe(p);
  const verRiv = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (p.sku) onRivales(p.sku);
  };
  return (
    <tr
      className={`fila${abierta ? " abierta" : ""}${movido ? " movida" : ""}`}
      onClick={(e) => {
        if ((e.target as Element).closest("a, button, input, select, summary")) return;
        onAlternar();
      }}
    >
      <td>
        <div className="pub-cel">
          <Mini serie={serie} i={p.img} px={44} alt={p.t} />
          <div>
            <div className="sku">{p.sku || "sin SKU"}</div>
            <a className="tit" href={p.u} target="_blank" rel="noopener noreferrer" title={p.t}>
              {p.t}
            </a>
            <div className="cuenta">
              {p.c}
              {p.st !== "active" ? " · " + p.st : ""}
            </div>
          </div>
        </div>
      </td>
      <td className="n">
        <div className="big" title="Lo que cobra Mercado Libre hoy a cualquier comprador (precio de venta, releído cada 5 min)">
          {pesos(m.precio)}
        </div>
        {m.desc > 0.004 ? (
          <div className="sm">
            <s>{pesos(m.normal)}</s> −{Math.round(m.desc * 100)} %
          </div>
        ) : (
          <div className="sm">sin descuento</div>
        )}
        {(() => {
          const cc = mejorCupon(m.precio, X.promo[p.id]?.cupones ?? []);
          if (cc == null || !(cc < m.precio - 0.5)) return null;
          const base = m.normal && m.normal > cc ? m.normal : m.precio;
          return (
            <div className="sm cupon" title="Lo que paga quien tiene un cupón vigente (seguidores, recompra, carrito): ML lo muestra como «% OFF con cupón»">
              con cupón {pesos(cc)} · −{Math.round((1 - cc / base) * 100)} %
            </div>
          );
        })()}
        {esAdmin && (
          <EditorPrecio id={p.id} cuenta={p.c} sku={p.sku} titulo={p.t} cobra={m.precio} normal={m.normal} bitacora={bitacoraDe(f, R, fotoGen)} />
        )}
      </td>
      <td className="promo-hoy">
        <CeldaPromoHoy X={X} id={p.id} />
      </td>
      <td className="n">
        {m.ref != null ? (
          <>
            <div>
              <span className="lbl-comp">media</span>
              <button className="ver-riv" type="button" onClick={verRiv} title={`Media (mediana) de ${c.n ?? "—"} comparables · ver lo scrapeado`}>
                {pesos(c.med)}
              </button>
            </div>
            {c.convertido ? (
              <div className="sm" title="Menos de 2 rivales en la misma cantidad: precio que dio el revisor convirtiendo packs por unidad">
                packs convertidos por el revisor
              </div>
            ) : (
              <div className="sm mono">
                {c.min ? `mín ${pesos(c.min)} · máx ${pesos(c.max)}` : "sin mín/máx"} · {c.n ?? "—"} comp.
              </div>
            )}
          </>
        ) : (
          <span className="sinref">
            <span className="pill p-none" title={c.motivo || ""}>
              {sinRef(c)}
            </span>
            {tieneRiv && (
              <button className="ver-riv chico" type="button" onClick={verRiv} title="Ver lo scrapeado">
                ver
              </button>
            )}
          </span>
        )}
      </td>
      <td className="n">
        <div className="big">{f.v.stock}</div>
        <div className="sm" title={f.v.vivo ? "Leído de Mercado Libre ahora" : "Sin lectura en vivo"}>
          {f.v.vivo ? "pzs ahora" : "pzs en FULL"}
        </div>
        {f.v.stock > 0 && f.v.stock <= LOW_STOCK && <span className="pill p-warn">low stock</span>}
      </td>
      <td className="n">
        <div className="mono">{f.v.u30 ?? m.u30} pzs</div>
        <div className="sm" title={f.v.u30 != null ? "Ventas de kubera, al minuto" : "De la foto"}>
          {f.v.vel.toFixed(1)} al día
        </div>
      </td>
      <td className="n mono">
        {pct1(f.v.conv)}
        <div className="sm" title={f.v.visVivo ? "Visitas de ML de los últimos 30 días (cada 30 min)" : "Sin lectura en vivo"}>
          {f.v.vis30.toLocaleString("es-MX")} visitas
        </div>
      </td>
      <td className="salud">
        <CeldaSalud salud={salud} id={p.id} />
      </td>
      <td className="n">
        {!(f.v.stock > 0) ? (
          <span className="pill p-bad">agotada</span>
        ) : isFinite(f.v.dias) ? (
          <>
            {f.v.dias < 1 ? "<1" : Math.round(f.v.dias)} días
            <div className="sm">{f.v.vivo ? desdeHoy(f.v.dias) : rel.fFecha(H + f.v.dias)}</div>
          </>
        ) : (
          <span className="sm">sin ventas</span>
        )}
      </td>
      <td className="n v7">
        <CeldaV7 f={f} />
      </td>
      <td>
        <span className={`pill ${cat[1]}`}>{cat[0]}</span>
        {editable(f) && (
          <>
            {" "}
            <span className="pill p-acc">editable</span>
          </>
        )}
      </td>
      <td className="hecho">
        <CeldaHecho X={X} id={p.id} />
      </td>
    </tr>
  );
}

/** El precio que deja el mejor cupón vigente a este precio (compra mínima y tope). Igual que el backend. */
function mejorCupon(precio: number, cupones: NonNullable<PricingPromoHoy["cupones"]>): number | null {
  let mejor = 0;
  for (const c of cupones) {
    if (c.minimo && precio < c.minimo) continue;
    let d = 0;
    if (c.monto) d = c.monto;
    else if (c.pct) d = Math.min((precio * c.pct) / 100, c.tope ?? Infinity);
    mejor = Math.max(mejor, d);
  }
  return mejor > 0 ? Math.round((precio - mejor) * 100) / 100 : null;
}

/** Hora local corta de un ISO. */
const horaCorta = (iso: string) =>
  new Date(iso).toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit", timeZone: "America/Mexico_City" });
/** «hace 4 min» / «hace 2 h». */
const haceCuanto = (iso: string) => {
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000));
  return min < 60 ? `hace ${min} min` : min < 1440 ? `hace ${Math.round(min / 60)} h` : `hace ${Math.round(min / 1440)} d`;
};
const TIPOS_RELAMPAGO = new Set(["LIGHTNING", "SMART_LIGHTNING"]);
/** Las candidatas en pocas fichas: una por promoción (relámpago primero), con su precio sugerido en el tooltip. */
function candidatasCortas(cs: NonNullable<PricingPromoHoy["candidatas"]>) {
  const vistas = new Map<string, { texto: string; tip: string; relampago: boolean; orden: number }>();
  cs.forEach((c) => {
    const relampago = TIPOS_RELAMPAGO.has(c.tipo);
    const texto = c.tipo === "DEAL" || c.tipo === "SMART" ? c.nombre : c.etiqueta;
    if (vistas.has(texto)) return;
    const rango = c.min != null && c.max != null ? ` · ML permite ${pesos(c.min)}–${pesos(c.max)}` : "";
    const pzs = c.stock_min != null ? ` · de ${c.stock_min} a ${c.stock_max} pzs` : "";
    const ml = c.meli_pct ? ` · ML pone ${c.meli_pct} %` : "";
    vistas.set(texto, {
      texto, relampago, orden: relampago ? 0 : c.tipo === "DOD" ? 1 : 2,
      tip: `${c.etiqueta}${c.nombre !== c.etiqueta ? " · " + c.nombre : ""}${c.sugerido ? " · sugerido " + pesos(c.sugerido) : ""}${rango}${pzs}${ml}`,
    });
  });
  return Array.from(vistas.values()).sort((a, b) => a.orden - b.orden);
}

/** La fecha en que se acaba, contando desde HOY. */
const desdeHoy = (dias: number) => {
  const d = new Date();
  d.setDate(d.getDate() + Math.round(dias));
  return d.toLocaleDateString("es-MX", { day: "numeric", month: "short", ...(d.getFullYear() !== new Date().getFullYear() ? { year: "numeric" } : {}) });
};
const fechaCorta = (iso: string) =>
  new Date(iso).toLocaleString("es-MX", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "America/Mexico_City" });
const diaCorto = (s: string) => new Date(s.length <= 10 ? s + "T12:00:00" : s).toLocaleDateString("es-MX", { day: "numeric", month: "short" });

// ── CALIDAD Y EXPERIENCIA DE ML (v0.659.0) ──────────────────────────────────
// Lo que guarda el barrido de calidad (enrich.listing_health). El nivel viene
// normalizado (bueno/medio/malo) y el texto es el de ML, tal cual.
const TONO_NIVEL: Record<string, string> = { bueno: "p-ok", medio: "p-warn", malo: "p-bad" };
const SIN_TEXTO: Record<string, string> = { no_calculada: "no la calcula ML", sin_datos: "sin datos" };

function FichaML({ q, que }: { q: PricingMetricaML | undefined; que: "Calidad" | "Experiencia" }) {
  if (!q) return <span className="pill p-none" title={`${que}: el barrido de calidad aún no mide esta publicación`}>{que}: —</span>;
  const medida = q.nivel != null;
  const cuando = q.medido ? ` · medida ${fechaCorta(q.medido)}` : "";
  const texto = medida ? q.texto ?? q.nivel : SIN_TEXTO[q.estado ?? ""] ?? "sin dato";
  const extra = q as PricingMetricaML & { pendientes?: number | null; razon?: string | null; consecuencia?: string | null; por_categoria?: boolean };
  const detalle = [
    `${que}: ${texto}${q.puntaje != null && que === "Calidad" ? ` (${Math.round(q.puntaje)}/100)` : ""}${cuando}`,
    extra.pendientes ? `${extra.pendientes} mejoras pendientes en ML` : "",
    extra.por_categoria ? "Calculada con las ventas de la categoría (la publicación tiene poca información)" : "",
    extra.razon ?? "",
    extra.consecuencia ?? "",
  ].filter(Boolean).join("\n");
  return (
    <span className={`pill ${medida ? TONO_NIVEL[q.nivel as string] ?? "p-none" : "p-none"}`} title={detalle}>
      {texto}
      {medida && que === "Calidad" && q.puntaje != null ? ` ${Math.round(q.puntaje)}` : ""}
    </span>
  );
}

/** «Calidad · Experiencia»: calidad de la publicación arriba y experiencia de compra abajo, con el detalle en el tooltip. */
function CeldaSalud({ salud, id }: { salud?: PricingSalud | null | "error"; id: string }) {
  if (salud == null) return <span className="sm">…</span>;
  if (salud === "error" || !salud.kubera) return <span className="sm" title="No se pudo leer /api/pricing/salud">sin lectura</span>;
  const s = salud.pubs[id];
  return (
    <div className="salud-cel">
      <div><span className="sm">calidad</span> <FichaML q={s?.c} que="Calidad" /></div>
      <div><span className="sm">experiencia</span> <FichaML q={s?.e} que="Experiencia" /></div>
    </div>
  );
}

// Color por tipo de promoción: relámpago resalta (dura horas); la nuestra, verde.
const TONO_PROMO: Record<string, string> = {
  LIGHTNING: "p-warn", DOD: "p-warn", SMART_LIGHTNING: "p-warn",
  PRICE_DISCOUNT: "p-ok", SELLER_CAMPAIGN: "p-acc", DEAL: "p-hold", SMART: "p-hold", UNHEALTHY_STOCK: "p-hold", PRICE_MATCHING: "p-hold",
};

/** «Promoción hoy»: qué pone el precio que se cobra, y encima cupones, «lleva más» y lo programado. */
function CeldaPromoHoy({ X, id }: { X: Extra; id: string }) {
  if (X.estado === "cargando") return <span className="sm">…</span>;
  if (X.estado === "error") return <span className="sm" title="No se pudo leer /api/pricing/hechos">sin dato</span>;
  const pr = X.promo[id];
  if (!pr) return <span className="sm" title="ML no devolvió el precio de esta publicación al leer las promociones">sin dato</span>;
  const montos = pr.cupones.map((c) => (c.monto ? `$${c.monto}` : `${c.pct} %`));
  const cupon = montos.length ? Array.from(new Set(montos)).join(" / ") : null;
  const otraNombre = pr.por?.nombre && pr.por.nombre !== pr.por.etiqueta ? pr.por.nombre : null;
  return (
    <div className="ph">
      {pr.por ? (
        <span className={`pill ${TONO_PROMO[pr.por.tipo] ?? "p-none"}`} title={pr.por.nombre ?? pr.por.etiqueta}>
          {pr.por.etiqueta}
        </span>
      ) : (
        <span className="pill p-none">Sin promoción</span>
      )}
      {otraNombre && <div className="sm ph-nom" title={otraNombre}>{otraNombre}</div>}
      {pr.cobra != null && (
        <div className="sm mono">
          cobra {pesos(pr.cobra)}
          {pr.normal && pr.normal > pr.cobra ? (
            <>
              {" "}
              <s>{pesos(pr.normal)}</s>
            </>
          ) : null}
        </div>
      )}
      <div className="ph-chips">
        {cupon && (
          <span className="chip" title={pr.cupones.map((c) => c.nombre).join(" · ")}>
            Cupón {cupon}
          </span>
        )}
        {pr.volumen && (
          <span className="chip" title={pr.volumen.nombre}>
            Lleva {pr.volumen.lleva ?? 2}: −{pr.volumen.pct ?? 0} %
          </span>
        )}
        {pr.programadas.slice(0, 2).map((x, k) => (
          <span key={k} className="chip prog" title={`${x.etiqueta}${x.precio ? " a " + pesos(x.precio) : ""}`}>
            {x.nombre} {x.desde ? diaCorto(x.desde) : ""}
          </span>
        ))}
      </div>
      {pr.candidatas && pr.candidatas.length > 0 && (
        <div className="ph-chips puede">
          <span className="lbl">puede entrar</span>
          {candidatasCortas(pr.candidatas).map((c, k) => (
            <span key={k} className={`chip ${c.relampago ? "rel" : ""}`} title={c.tip}>
              {c.texto}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** «Hecho»: el precio que sí se movió en ML, o SIN MOVIMIENTO DE PRECIO. */
function CeldaHecho({ X, id }: { X: Extra; id: string }) {
  if (X.estado === "cargando") return <span className="sm">…</span>;
  if (X.estado === "error") return <span className="sm" title="No se pudo leer /api/pricing/hechos">sin dato</span>;
  const h = X.hecho.get(id);
  if (!h) return <span className="sin-mov">SIN MOVIMIENTO DE PRECIO</span>;
  const baja = h.antes > 0 ? 1 - h.nuevo / h.antes : 0;
  const desc = h.normal && h.normal > h.nuevo ? 1 - h.nuevo / h.normal : null;
  const d = h.despues;
  const otroPrecio = d?.cobra_hoy != null && Math.abs(d.cobra_hoy - h.nuevo) >= 0.01;
  return (
    <div className="hc">
      <span className="pill p-ok">Hecho {diaCorto(h.desde)}</span>
      <div className="hc-precio">
        {pesos(h.antes)} → <b>{pesos(h.nuevo)}</b> <span className="pct">−{(baja * 100).toFixed(1).replace(".0", "")} %</span>
      </div>
      {desc != null && (
        <div className="sm">
          se ve <s>{pesos(h.normal)}</s> −{Math.round(desc * 100)} %{h.hasta ? ` · hasta ${diaCorto(h.hasta)}` : ""}
        </div>
      )}
      {h.se_ve === false && <div className="hc-aviso">ML no lo muestra como descuento</div>}
      {d ? (
        <div className={d.pzs > 0 ? "hc-vende" : "sm"}>
          después: {d.pzs > 0 ? `${d.pzs} pzs · ${pesos(d.pesos)}` : "sin ventas aún"}
          {otroPrecio ? ` · cobra ${pesos(d.cobra_hoy)}` : ""}
        </div>
      ) : (
        <div className="sm">sin lectura de ventas</div>
      )}
    </div>
  );
}

function CeldaV7({ f }: { f: Fila }) {
  const x = f.x;
  const hoy = (
    <div>
      <span className="lbl">hoy</span>
      {pzs(x.hoy)} pzs · {pesos(x.hoy * x.pHoy)}
    </div>
  );
  if (x.sug == null)
    return (
      <>
        {hoy}
        <div>
          <span className="lbl">sug.</span>
          <span className="sm">sin cambio</span>
        </div>
      </>
    );
  const cls = x.sug > x.hoy + 0.05 ? "mas" : x.sug < x.hoy - 0.05 ? "menos" : "";
  const tip = x.estimado
    ? "Estimado: sin ventas en 30 días, se usan sus visitas y la conversión típica al precio de mercado"
    : "Ritmo de hoy ajustado por el cambio de precio";
  return (
    <>
      {hoy}
      <div title={tip}>
        <span className="lbl">sug.</span>
        <span className={cls}>
          {x.estimado ? "≈" : ""}
          {pzs(x.sug)} pzs · {pesos(x.sug * (x.pSug ?? 0))}
        </span>
      </div>
    </>
  );
}

// Sin columna en la tabla desde v0.660.0 (Eduardo, 16-oct): «Qué hacer» sigue en los chips de arriba y en el
// detalle de cada publicación. Se conserva para volver a ponerla sin reescribirla.
function CeldaAccion({ f, R }: { f: Fila; R: Reglas }) {
  const { m, a } = f;
  const texto = a.t ?? etiqueta(a.k, R);
  if (!a.precio) return <span className={`pill p-${SENTIDO[a.k]}`}>{texto}</span>;
  const d = descuentoPara(m.normal, a.precio);
  const cambio = a.precio / m.precio - 1;
  // SIN VENTAS: el ajuste se lee como la PROMOCIÓN que hay que poner en ML —
  // precio normal tachado → precio con promoción y su % — y debajo cuánto
  // cambia contra lo que se cobra hoy.
  if (ES_SV(a.k)) {
    const normal = m.normal && m.normal > 0 ? m.normal : m.precio;
    const desc = a.descNuevo ?? (normal > 0 ? 1 - a.precio / normal : 0);
    return (
      <>
        <span className={`pill p-${SENTIDO[a.k]}`}>{texto}</span>
        <div className="promo" title={`Promoción en ML: precio normal ${pesos(normal)} con ${Math.round(desc * 100)} % de descuento`}>
          <span className="lbl">promo</span>
          <s>{pesos(normal)}</s> → <b>{pesos(a.precio)}</b> <span className="pct">−{Math.round(desc * 100)} %</span>
        </div>
        <div className="luego">
          hoy {pesos(m.precio)}
          {m.desc > 0.004 ? ` (−${Math.round(m.desc * 100)} %)` : " sin promo"} ·{" "}
          {cambio > 0 ? "+" : "−"}
          {Math.abs(Math.round(cambio * 100))} %
        </div>
        {a.luego && (
          <div
            className="luego"
            title={R.svLuego === "normal"
              ? `Si en 7 días no vende: precio normal ${pesos(a.luego.normal)} y se sigue cobrando ${pesos(a.luego.precio)} (${Math.round(a.luego.desc * 100)} % de descuento)`
              : `Si en 7 días no vende: cobrar ${pesos(a.luego.precio)} (precio normal ${pesos(a.luego.normal)} con ${Math.round(a.luego.desc * 100)} % de descuento)`}
          >
            7 d sin venta:{" "}
            {R.svLuego === "normal"
              ? `normal ${pesos(a.luego.normal)} (−${Math.round(a.luego.desc * 100)} %)`
              : `${pesos(a.luego.precio)} (−${Math.round(a.luego.desc * 100)} %)`}
          </div>
        )}
      </>
    );
  }
  const titulo = d
    ? d.subirNormal
      ? "Hay que subir el precio normal"
      : `Descuento de ${Math.round(d.desc * 100)} % sobre el precio normal de ${pesos(m.normal)}`
    : "";
  return (
    <>
      <span className={`pill p-${SENTIDO[a.k]}`}>{texto}</span>
      <div className="obj" title={titulo}>
        {pesos(a.precio)}{" "}
        <span className="sm">
          {pctS(cambio)}
          {d ? " · " + (d.subirNormal ? "subir normal" : "desc " + Math.round(d.desc * 100) + " %") : ""}
        </span>
      </div>
      {a.luego && (
        <div className="luego" title="Lo que sigue si en 7 días no vende">
          sin venta en 7 d →{" "}
          {R.svLuego === "normal"
            ? `normal ${pesos(a.luego.normal)} (−${Math.round(a.luego.desc * 100)} %)`
            : `${pesos(a.luego.precio)} (−${Math.round(a.luego.desc * 100)} %)`}
        </div>
      )}
    </>
  );
}

/**
 * La publicación de la foto con las ventas y visitas de AHORA (v0.663.0): la gráfica llega hasta hoy.
 * Las visitas de ML pisan las de la foto en los días que trae; los días sin dato después de la foto van en 0.
 * Si kubera no contestó, las ventas se quedan como en la foto.
 */
function pubConSerie(p: PricingPub, s: PricingSerie, rel: Reloj): PricingPub {
  const dia = (d: string) => Math.round((new Date(d + "T12:00:00").getTime() - rel.D0.getTime()) / 864e5);
  const H = Math.max(p.vis.length - 1, dia(s.hoy));
  const vis = Array.from({ length: H + 1 }, (_, i) => p.vis[i] ?? 0);
  Object.entries(s.visitas).forEach(([d, n]) => {
    const i = dia(d);
    if (i >= 0 && i <= H) vis[i] = n;
  });
  const o = s.kubera
    ? s.ventas
        .map((v): PricingOrden => [dia(v.dia), v.precio, v.piezas, v.cancelada ? 1 : 0])
        .filter((x) => x[0] >= 0 && x[0] <= H)
    : p.o;
  return { ...p, o, vis };
}

function Detalle({ f, R, rel, nRiv, onRivales }: { f: FilaV; R: Reglas; rel: Reloj; nRiv: number; onRivales: (sku: string) => void }) {
  const { p } = f;
  const c = p.comp || {};
  const cat = p.cat;
  const catT = CAT_T[cat.v] ?? CAT_T.revisar;
  // Ventas y visitas de ahora para la gráfica (v0.663.0): la foto es del 14-oct y no veía lo vendido después.
  const [serie, setSerie] = useState<PricingSerie | "cargando" | "error" | null>(null);
  useEffect(() => {
    const ctl = new AbortController();
    setSerie("cargando");
    pricingSerie(p.id, p.c, ctl.signal)
      .then((d) => {
        if (!ctl.signal.aborted) setSerie(d);
      })
      .catch(() => {
        if (!ctl.signal.aborted) setSerie("error");
      });
    return () => ctl.abort();
  }, [p.id, p.c]);
  // Con la serie de ahora (y el stock de ML de ahora) se recalcula todo el detalle: «Qué hacer y por qué», lo que
  // vendía antes y después, y la proyección para agotar. La foto sola decía «sin ventas» de lo que ya vendió.
  const pV = useMemo(() => {
    if (!serie || serie === "cargando" || serie === "error") return p;
    const conSerie = pubConSerie(p, serie, rel);
    return f.v.vivo ? { ...conSerie, stock: f.v.stock } : conSerie;
  }, [p, serie, rel, f.v.vivo, f.v.stock]);
  const m = useMemo(() => (pV === p ? f.m : metricas(pV, R)), [pV, p, f.m, R]);
  const a = useMemo(() => (pV === p ? f.a : accion(pV, m, R)), [pV, p, f.a, m, R]);
  const h = m.hist;
  // Motivos de cancelaciones y devoluciones (v0.659.0): se piden al abrir el detalle, solo si hay algo que explicar.
  const [motivos, setMotivos] = useState<PricingMotivos | "cargando" | "error" | null>(null);
  useEffect(() => {
    if (!pV.o.length) return;
    const ctl = new AbortController();
    setMotivos("cargando");
    pricingMotivos(p.id, ctl.signal)
      .then((d) => {
        if (!ctl.signal.aborted) setMotivos(d);
      })
      .catch(() => {
        if (!ctl.signal.aborted) setMotivos("error");
      });
    return () => ctl.abort();
  }, [p.id, pV.o.length]);
  return (
    <div className="det-caja">
      <div className="det-top">
        <div>
          <h4>Qué hacer y por qué</h4>
          <p>
            <b>{a.t ?? etiqueta(a.k, R)}.</b> {a.por}
          </p>
        </div>
        <div>
          <h4>Competencia</h4>
          <p>
            {m.ref != null
              ? `${R.ref === "min" ? "El más barato" : "Mediana"} ${pesos(m.ref)} de ${c.n ?? "—"} comparables` +
                `${c.min ? " (de " + pesos(c.min) + " a " + pesos(c.max) + ")" : ""}. Fuente: ${c.fuente || "—"}${
                  c.convertido ? "; el revisor convirtió packs por unidad" : ""
                }.`
              : c.motivo || "Sin precio de competencia fiel."}
            {c.nota && <span className="sm"> {c.nota}</span>}
            {c.visual && (
              <>
                <br />
                <span className="sm">
                  Revisión con fotos:{" "}
                  {c.visual.no || c.visual.no_se
                    ? `de ${c.visual.n} rivales «mismo», ${c.visual.no} eran otro producto y ${c.visual.no_se} no mostraban el producto`
                    : `los ${c.visual.n} rivales coinciden`}
                  {c.visual.busqueda ? "; la búsqueda trajo otro producto" : ""}
                  {c.visual.distintas ? "; nuestras publicaciones de este SKU muestran productos distintos" : ""}.
                </span>
              </>
            )}
            {nRiv > 0 && p.sku && (
              <>
                <br />
                <button className="ver-riv chico" type="button" onClick={() => onRivales(p.sku as string)}>
                  Ver las {nRiv} publicaciones scrapeadas
                </button>
              </>
            )}
          </p>
        </div>
        <div>
          <h4>Categoría</h4>
          <p>
            <span className={`pill ${catT[1]}`}>{catT[0]}</span> {cat.actual || ""}. {cat.sug ? `Sugerida: ${cat.sug}. ` : ""}
            <span className="sm">{cat.motivo || ""}</span>
            {editable(f) ? (
              <>
                <br />
                <b>Se puede cambiar:</b> la publicación no tiene ventas.
              </>
            ) : cat.v === "incorrecta" ? (
              <>
                <br />
                <span className="sm">Con ventas, ML no deja cambiarla.</span>
              </>
            ) : null}
          </p>
        </div>
        {h && (
          <div>
            <h4>Historia de precio</h4>
            <p>
              Vendía sobre todo a {pesos(h.pA)} hasta el {rel.fFecha(h.dHi)}. Antes: {h.vA.toFixed(2)} al día
              {h.cA != null ? ", conversión " + pct1(h.cA) : ""}. Después: {h.vB.toFixed(2)} al día
              {h.cB != null ? ", conversión " + pct1(h.cB) : ""}.{h.dispara ? " Bajar el precio no ayudó." : " Bajar el precio sí ayudó."}
            </p>
          </div>
        )}
      </div>
      <div className="leyenda">
        <span>
          <i style={{ background: "var(--accent)" }} />
          venta · el número son las piezas
        </span>
        <span>
          <i style={{ background: "transparent", border: "1.5px solid var(--muted)" }} />
          cancelada
        </span>
        <span>
          <i style={{ background: "var(--accent-soft)", borderRadius: 2 }} />
          visitas de la semana
        </span>
        <span>
          <i style={{ background: "var(--proj)", border: "1px solid var(--line)", borderRadius: 2 }} />
          proyección para agotar
        </span>
        <span title="Ventas de kubera (las escribe el webhook al momento) y visitas por día de Mercado Libre">
          {serie === "cargando" || serie === null
            ? "Leyendo las ventas de hoy…"
            : serie === "error" || !serie.kubera
              ? "Sin lectura en vivo: ventas y visitas de la foto"
              : `En vivo · ${horaCorta(serie.leido)}` +
                (serie.visitas_ok ? "" : " · ML no dio las visitas nuevas (van en 0)") +
                (serie.sin_bruto
                  ? ` · ${serie.sin_bruto} venta${serie.sin_bruto > 1 ? "s" : ""} con precio neto (ML no dio el que pagó el comprador)`
                  : "")}
        </span>
      </div>
      <Grafica f={pV === p ? f : { ...f, p: pV, m, a }} R={R} rel={rel} motivos={motivos} />
    </div>
  );
}

// ── Reglas y umbrales ───────────────────────────────────────────────────────

type CampoNum = { r: keyof Reglas; t: string; step: number; min?: number; max?: number; pct?: boolean; nota?: string };

const CAMPOS_JOSE: CampoNum[] = [
  { r: "altas", t: "Ventas altas desde (piezas al día)", step: 0.1, min: 0, nota: "«3 al día aprox»" },
  { r: "bajas", t: "Ventas bajas desde (piezas al día)", step: 0.1, min: 0, nota: "«1–2 al día»; abajo de esto son pocas ventas" },
  { r: "conv", t: "Conversión para subir (%)", step: 0.1, min: 0, pct: true, nota: "arriba de esto sube; en esto o abajo, no se mueve" },
  { r: "diasStock", t: "Stock para subir (días o menos)", step: 1, min: 0 },
  { r: "banda", t: "«Al nivel» de la competencia (± %)", step: 0.5, min: 0, pct: true },
  { r: "paso", t: "Cuánto subir (%)", step: 0.5, min: 0, pct: true },
  { r: "tolHist", t: "«Vende igual» tras bajar (± %)", step: 1, min: 0, pct: true },
  { r: "minHist", t: "Piezas a precio alto para juzgar", step: 1, min: 1 },
  { r: "elasticidad", t: "Elasticidad precio (venta 7 d)", step: 0.1, max: 0, nota: "−2 = bajar 10 % el precio vende ~23 % más" },
];

const CAMPOS_SV: CampoNum[] = [
  { r: "svAgresivo", t: "Descuento «super agresivo»: más de (%)", step: 1, min: 0, pct: true },
  { r: "svExtraAgresivo", t: "Si ya es agresivo: agregar esta semana (%)", step: 0.5, min: 0, pct: true, nota: "solo esto; nada más" },
  { r: "svMostrar", t: "Si en 7 días no vende: mostrar (%)", step: 1, min: 0, max: 95, pct: true },
  { r: "svExtra", t: "Con descuento (agresivo o menos): agregar (%)", step: 0.5, min: 0, pct: true },
  { r: "svCorte", t: "Sin descuento: ticket bajo, menor a ($)", step: 10, min: 0 },
  { r: "svBajo", t: "Sin descuento, ticket bajo (%)", step: 0.5, min: 0, pct: true },
  { r: "svAlto", t: "Sin descuento, ticket alto (%)", step: 0.5, min: 0, pct: true },
];

function Numero({ campo, R, cambiar }: { campo: CampoNum; R: Reglas; cambiar: (p: Partial<Reglas>) => void }) {
  const actual = R[campo.r] as number;
  const mostrar = (v: number) => String(campo.pct ? +(v * 100).toFixed(2) : v);
  const [texto, setTexto] = useState(mostrar(actual));
  useEffect(() => setTexto(mostrar(actual)), [actual]); // eslint-disable-line react-hooks/exhaustive-deps
  const id = `pm-r-${campo.r}`;
  return (
    <label htmlFor={id}>
      {campo.t}
      <input
        id={id}
        type="number"
        step={campo.step}
        min={campo.min}
        max={campo.max}
        value={texto}
        onChange={(e) => {
          setTexto(e.target.value);
          const v = parseFloat(e.target.value);
          if (e.target.value.trim() !== "" && isFinite(v)) cambiar({ [campo.r]: campo.pct ? v / 100 : v } as Partial<Reglas>);
        }}
        onBlur={() => setTexto(mostrar(actual))}
      />
      {campo.nota && <small>{campo.nota}</small>}
    </label>
  );
}

function PanelReglas({ R, cambiar }: { R: Reglas; cambiar: (p: Partial<Reglas> | null) => void }) {
  return (
    <details className="reglas">
      <summary>Reglas y umbrales</summary>

      <h3>Productos sin ventas · las 2 cuentas de ML (15-oct)</h3>
      <div className="reglas-grid">
        <label htmlFor="pm-r-sv">
          Aplicar las reglas de sin ventas
          <select id="pm-r-sv" value={R.sinVentas ? "si" : "no"} onChange={(e) => cambiar({ sinVentas: e.target.value === "si" })}>
            <option value="si">Sí: primero que las de competencia</option>
            <option value="no">No: el monitor como lo dejó José</option>
          </select>
          <small>cero piezas pagadas en 30 días, con stock en FULL</small>
        </label>
        {CAMPOS_SV.map((c) => (
          <Numero key={c.r} campo={c} R={R} cambiar={cambiar} />
        ))}
        <label htmlFor="pm-r-svmodo">
          Cómo se suma el % adicional
          <select id="pm-r-svmodo" value={R.svModo} onChange={(e) => cambiar({ svModo: e.target.value as Reglas["svModo"] })}>
            <option value="puntos">Al descuento: 62 % → 65 %</option>
            <option value="precio">Al precio de hoy: × 0.97</option>
          </select>
        </label>
        <label htmlFor="pm-r-svluego">
          Si no vende: cómo se llega a mostrar ese %
          <select id="pm-r-svluego" value={R.svLuego} onChange={(e) => cambiar({ svLuego: e.target.value as Reglas["svLuego"] })}>
            <option value="normal">Bajar el precio normal (se cobra lo mismo)</option>
            <option value="precio">Cobrar el precio normal menos ese %</option>
          </select>
        </label>
      </div>

      <h3>Con ventas · reglas de José (14-oct)</h3>
      <div className="reglas-grid">
        {CAMPOS_JOSE.map((c) => (
          <Numero key={c.r} campo={c} R={R} cambiar={cambiar} />
        ))}
        <label htmlFor="pm-r-ref">
          Precio de competencia de referencia
          <select id="pm-r-ref" value={R.ref} onChange={(e) => cambiar({ ref: e.target.value as Reglas["ref"] })}>
            <option value="med">Mediana de los comparables</option>
            <option value="min">El más barato</option>
          </select>
        </label>
      </div>
      <div className="reglas-acciones">
        <button className="boton" type="button" onClick={() => cambiar(null)}>
          Volver a las reglas originales
        </button>
        <span>
          El orden en que se aplican: sin stock → sin ventas (agresivo / con descuento / sin descuento) → bajar no ayudó → sin
          referencia → ventas altas → ventas bajas → pocas ventas (arriba / abajo / al nivel).
        </span>
      </div>
    </details>
  );
}

function Notas({ R, convTipica, fallas }: { R: Reglas; convTipica: number | null; fallas: number }) {
  const p = (x: number) => `${+(x * 100).toFixed(1)} %`;
  return (
    <section className="notas">
      <p>
        <b>Sin ventas (Brandon, 15-oct).</b> Las publicaciones de las dos cuentas con cero piezas pagadas en 30 días y stock en
        FULL siguen otras reglas, antes que las de competencia: con descuento de más de {p(R.svAgresivo)}, solo{" "}
        {p(R.svExtraAgresivo)} más esta semana (si vende, mantener; si no, que muestre {p(R.svMostrar)} de descuento); sin
        descuento, {p(R.svBajo)} si el ticket es menor a {pesos(R.svCorte)} y {p(R.svAlto)} si es mayor; con descuento de{" "}
        {p(R.svAgresivo)} o menos, {p(R.svExtra)} más.{" "}
        {R.svModo === "puntos"
          ? "El % adicional se suma al descuento sobre el precio normal (62 % → 65 %)."
          : "El % adicional se descuenta del precio de hoy."}{" "}
        La foto no sabe si ya se aplicó el ajuste de la semana: se ve en la siguiente foto, por las ventas.
      </p>
      <p>
        <b>De dónde sale cada número.</b> Precio hoy: lo que paga el comprador según ML (<span className="mono">sale_price</span>
        ); el tachado es el precio normal. Ventas y conversión: piezas pagadas de los últimos 30 días (órdenes de kubera) entre
        visitas de ML del mismo periodo. «Al día» usa los últimos 14 días si hubo 3 o más piezas; si no, 30. Se agota: piezas en
        FULL entre ventas al día.
      </p>
      <p>
        <b>Competencia.</b> Mediana de los rivales que la revisión del 14-oct marcó como el mismo producto en la misma cantidad
        (la regla del juez de OMNICANAL). Después se revisó cada rival con su FOTO contra la nuestra: lo que era otro producto se
        descartó y lo que no mostraba el producto quedó como dudoso (no cuenta). Si quedaron menos de 2 solo por packs, se usa el
        precio que dio el revisor convirtiendo packs, marcado «convertido». Las categorías de adultos no se miden: ML esconde esos
        productos al buscador. Toca el precio para ver todo lo scrapeado y por qué contó o no cada rival.
      </p>
      <p>
        <b>Categoría.</b> «Correcta» si la revisión lo dijo o si ML sugiere esa misma categoría para el título; «revisar», las
        discutibles. «Editable»: incorrecta y sin ventas, la única que ML deja cambiar.
      </p>
      <p>
        <b>Supuestos.</b> «Ventas bajas» va de {R.bajas} a menos de {R.altas} al día; abajo de {R.bajas} al día cuenta como pocas
        ventas. La conversión es la de 30 días (no hay CTR por publicación). Venta 7 d: «hoy» es el ritmo actual × 7; «sug.»
        ajusta ese ritmo por el cambio de precio con una elasticidad de {R.elasticidad} (medida con 31 pares de publicaciones de
        dos cuentas). Sin ventas, «≈» estima con sus visitas y la conversión típica al precio de mercado (
        {convTipica == null ? "—" : (convTipica * 100).toFixed(1) + " %"}, la mediana de las nuestras que venden y están al nivel
        o abajo). Nunca más que el stock. «Bajar no ayudó» compara los 30 días antes de la última venta a precio alto (≥ 5 %
        arriba del de hoy) contra lo que va desde entonces. Todos los umbrales se cambian en «Reglas y umbrales».
      </p>
      {fallas > 0 && <p className="sm">Lecturas de ML que fallaron al armar la foto: {fallas}.</p>}
    </section>
  );
}
