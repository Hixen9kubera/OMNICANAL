"use client";

/**
 * /analisis/categorias — Ventas por categoría (árbol COMPLETO de ML) y por
 * cuenta, hasta la publicación individual.
 *
 * Réplica en vivo del reporte ventas_por_categoria de José (xlsx del 19-jul,
 * Drive) con su mismo drill: categoría → subcategorías (todos los niveles de
 * la ruta de ML, hasta 7) → publicaciones (MLM…, tienda, título, situación,
 * uds, $, precio). El backend manda las HOJAS con su ruta y aquí se arma el
 * árbol con acumulados por nivel; las publicaciones se piden al expandir una
 * hoja (endpoint /categorias/publicaciones).
 *
 * Diferencia declarada vs el xlsx: "Días en venta" no existe — listings no
 * guarda la fecha de creación de la publicación. Se muestra la 1ª VENTA
 * registrada del período, que es lo que sí sabemos.
 */

import { useEffect, useMemo, useState } from "react";
import { ChevronRight, Download, Loader2, Search, X } from "lucide-react";
import { API_BASE } from "@/lib/api";
import Ayuda from "@/components/Ayuda";

interface CuentaVenta { cuenta: string; uds: number; venta: number }

interface Hoja {
  ruta: string;
  category_id: string | null;
  uds: number;
  venta: number;
  skus: number;
  publicaciones: number;
  activas: number;
  cuentas: CuentaVenta[] | null;
}

interface Resp {
  ambiente: string;
  dias: number;
  cuenta: string | null;
  totales: { venta: number; uds: number; categorias: number };
  hojas: Hoja[];
}

interface Pub {
  item_id: string | null;
  cuenta: string;
  sku: string | null;
  uds: number;
  venta: number;
  primera_venta: string;
  ultima_venta: string;
  situacion: string | null;
  precio: number | null;
  titulo: string | null;
}

/* Nodo del árbol armado en cliente a partir de las rutas de las hojas. */
interface Nodo {
  label: string;
  clave: string;                    // ruta acumulada (única en el árbol)
  hijos: Nodo[];
  category_id: string | null;       // solo las hojas lo tienen
  uds: number;
  venta: number;
  skus: number;
  publicaciones: number;
  activas: number;
  cuentas: Map<string, { uds: number; venta: number }>;
}

const n = (v: number | string | null | undefined) => (v == null ? 0 : Number(v));
const fMoney = (v: number | string | null | undefined, dec = 0) =>
  v == null ? "—" : `$${n(v).toLocaleString("es-MX", { maximumFractionDigits: dec })}`;
const fNum = (v: number | string | null | undefined, dec = 0) =>
  v == null ? "—" : n(v).toLocaleString("es-MX", { maximumFractionDigits: dec });

const CUENTAS = [
  { id: "", label: "Consolidado" },
  { id: "BEKURA", label: "Bekura" },
  { id: "SANCORFASHION", label: "Sancor" },
  { id: "AMAZON", label: "Amazon" },
];
const PERIODOS = [
  { dias: 7, label: "7 días" },
  { dias: 30, label: "30 días" },
  { dias: 60, label: "60 días" },
  { dias: 90, label: "90 días" },
  { dias: 400, label: "Histórico" },
];
const CUENTA_INI: Record<string, string> = {
  BEKURA: "BK", SANCORFASHION: "SC", AMAZON: "AMZ",
};
const CUENTA_CHIP: Record<string, string> = {
  BEKURA: "bg-sky-100 text-sky-700",
  SANCORFASHION: "bg-violet-100 text-violet-700",
  AMAZON: "bg-amber-100 text-amber-700",
};
const SIT_CHIP: Record<string, string> = {
  active: "bg-emerald-100 text-emerald-700",
  paused: "bg-amber-100 text-amber-700",
};

const AYUDA: Record<string, { titulo: string; texto: string }> = {
  cat: { titulo: "Categoría", texto: "El árbol COMPLETO de ML ('Deportes › Fitness y Musculación › … › Caminadoras'), con acumulados en cada nivel. La taxonomía es de ML pero se aplica por SKU, así que las ventas de Amazon también se clasifican. Expandir una subcategoría final muestra sus publicaciones una por una." },
  pub: { titulo: "Publicaciones", texto: "Listados vivos (no cerrados) en ML y Amazon de esa rama, vendan o no — y cuántos están ACTIVOS. Muchas publicaciones con pocas activas = catálogo pausado." },
  skus: { titulo: "SKUs con venta", texto: "Productos distintos con al menos una venta en el período. En los niveles agrupados es la suma de sus ramas: un SKU clasificado en dos subcategorías cuenta en ambas." },
  uds: { titulo: "Unidades", texto: "Piezas vendidas en el período, sumando las cuentas seleccionadas." },
  venta: { titulo: "Ventas $", texto: "Importe vendido en el período. Venta bruta: no descuenta comisión ni costo." },
  pct: { titulo: "% del total", texto: "Qué parte de la venta del período aporta esta rama. La barra compara contra la categoría más grande." },
  prom: { titulo: "Precio promedio", texto: "Ventas $ entre unidades: el ticket promedio REAL al que salió la rama (no el precio de lista)." },
  cuentas: { titulo: "Por cuenta", texto: "Desglose de las unidades entre Bekura, Sancor y Amazon. El importe de cada cuenta va en el tooltip." },
};

function armarArbol(hojas: Hoja[]): Nodo[] {
  const raiz: Nodo[] = [];
  const buscar = (nivel: Nodo[], label: string, clave: string): Nodo => {
    let x = nivel.find((h) => h.label === label);
    if (!x) {
      x = { label, clave, hijos: [], category_id: null, uds: 0, venta: 0,
            skus: 0, publicaciones: 0, activas: 0, cuentas: new Map() };
      nivel.push(x);
    }
    return x;
  };
  for (const h of hojas) {
    const tramos = String(h.ruta).split("›").map((t) => t.trim()).filter(Boolean);
    if (!tramos.length) tramos.push("Sin categoría");
    let nivel = raiz, clave = "";
    tramos.forEach((tramo, i) => {
      clave = clave ? `${clave}›${tramo}` : tramo;
      const nodo = buscar(nivel, tramo, clave);
      nodo.uds += h.uds;
      nodo.venta += n(h.venta);
      nodo.skus += h.skus;
      nodo.publicaciones += h.publicaciones;
      nodo.activas += h.activas;
      for (const c of h.cuentas ?? []) {
        const acc = nodo.cuentas.get(c.cuenta) ?? { uds: 0, venta: 0 };
        acc.uds += c.uds; acc.venta += n(c.venta);
        nodo.cuentas.set(c.cuenta, acc);
      }
      if (i === tramos.length - 1) nodo.category_id = h.category_id;
      nivel = nodo.hijos;
    });
  }
  const ordenar = (xs: Nodo[]) => {
    xs.sort((a, b) => b.venta - a.venta);
    xs.forEach((x) => ordenar(x.hijos));
  };
  ordenar(raiz);
  return raiz;
}

function Kpi({ label, value, pie }: { label: string; value: string; pie?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-bold text-slate-900">{value}</div>
      {pie && <div className="mt-0.5 text-[11px] text-slate-400">{pie}</div>}
    </div>
  );
}

export default function CategoriasPage() {
  const [cuenta, setCuenta] = useState("");
  const [dias, setDias] = useState(60);
  // Período ABSOLUTO (X a X): si hay fechas, mandan sobre los botones de días.
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const rangoActivo = Boolean(desde || hasta);
  const [datos, setDatos] = useState<Resp | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [abiertas, setAbiertas] = useState<Set<string>>(new Set());
  const [pubs, setPubs] = useState<Record<string, Pub[] | "cargando">>({});
  const [q, setQ] = useState("");

  useEffect(() => {
    let vivo = true;
    setCargando(true); setError(null);
    setAbiertas(new Set()); setPubs({});
    const q = new URLSearchParams({ dias: String(dias) });
    if (cuenta) q.set("cuenta", cuenta);
    if (desde) q.set("desde", desde);
    if (hasta) q.set("hasta", hasta);
    fetch(`${API_BASE}/api/fulfillment/categorias?${q}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : r.json().then((e) => Promise.reject(e.detail ?? r.status))))
      .then((d: Resp) => { if (vivo) setDatos(d); })
      .catch((e) => { if (vivo) setError(String(e)); })
      .finally(() => { if (vivo) setCargando(false); });
    return () => { vivo = false; };
  }, [cuenta, dias, desde, hasta]);

  /* La búsqueda filtra por la RUTA completa: "caminadora" encuentra la hoja
     Caminadoras aunque cuelgue 4 niveles abajo, y "deportes" trae la rama
     entera. Con búsqueda activa el árbol se expande solo (queda chico); las
     publicaciones sí siguen pidiéndose al clic. */
  const buscando = q.trim().length > 0;
  const arbol = useMemo(() => {
    const hojas = datos?.hojas ?? [];
    // Sin búsqueda: solo lo que vendió (el árbol de siempre). Buscando: TAMBIÉN
    // las categorías con catálogo y 0 ventas — "Caminadoras" en 60 días debe
    // responder "existe y no vendió", no "no existe" (Eduardo, 31-jul).
    if (!buscando) return armarArbol(hojas.filter((h) => h.uds > 0));
    const t = q.trim().toLowerCase();
    return armarArbol(hojas.filter((h) => String(h.ruta).toLowerCase().includes(t)));
  }, [datos, q, buscando]);
  const ventaTotal = n(datos?.totales.venta);
  const maxPct = useMemo(
    () => Math.max(1, ...arbol.map((c) => (ventaTotal ? (c.venta / ventaTotal) * 100 : 0))),
    [arbol, ventaTotal]);

  const alternar = (nodo: Nodo) => {
    setAbiertas((prev) => {
      const s = new Set(prev);
      if (s.has(nodo.clave)) { s.delete(nodo.clave); return s; }
      s.add(nodo.clave);
      return s;
    });
    // Hoja del árbol → pedir sus publicaciones una sola vez
    if (!nodo.hijos.length && nodo.category_id && !pubs[nodo.category_id]) {
      const id = nodo.category_id;
      setPubs((p) => ({ ...p, [id]: "cargando" }));
      const q = new URLSearchParams({ categoria_id: id, dias: String(dias) });
      if (cuenta) q.set("cuenta", cuenta);
      if (desde) q.set("desde", desde);
      if (hasta) q.set("hasta", hasta);
      fetch(`${API_BASE}/api/fulfillment/categorias/publicaciones?${q}`, { cache: "no-store" })
        .then((r) => r.json())
        .then((d) => setPubs((p) => ({ ...p, [id]: d.items ?? [] })))
        .catch(() => setPubs((p) => ({ ...p, [id]: [] })));
    }
  };

  const t = datos?.totales;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
          {CUENTAS.map((c) => (
            <button key={c.id} onClick={() => setCuenta(c.id)}
                    className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                      cuenta === c.id
                        ? "bg-indigo-600 font-semibold text-white"
                        : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}>
              {c.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
          {PERIODOS.map((p) => (
            <button key={p.dias} onClick={() => { setDias(p.dias); setDesde(""); setHasta(""); }}
                    className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                      dias === p.dias && !rangoActivo
                        ? "bg-slate-900 font-semibold text-white"
                        : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}>
              {p.label}
            </button>
          ))}
        </div>
        {/* Período absoluto (X a X): manda sobre los botones de días */}
        <div className={`flex items-center gap-1.5 rounded-xl border p-1.5 shadow-sm ${
          rangoActivo ? "border-slate-900 bg-white" : "border-slate-200 bg-white"}`}>
          <input type="date" value={desde} max={hasta || undefined}
                 onChange={(e) => setDesde(e.target.value)}
                 className="rounded-lg px-2 py-1 text-sm text-slate-600 outline-none" />
          <span className="text-xs text-slate-400">a</span>
          <input type="date" value={hasta} min={desde || undefined}
                 onChange={(e) => setHasta(e.target.value)}
                 className="rounded-lg px-2 py-1 text-sm text-slate-600 outline-none" />
          {rangoActivo && (
            <button onClick={() => { setDesde(""); setHasta(""); }}
                    title="Volver a los períodos relativos"
                    className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
              <X size={14} />
            </button>
          )}
        </div>
        <a href={`${API_BASE}/api/fulfillment/categorias/excel?${(() => {
             const q = new URLSearchParams({ dias: String(dias) });
             if (cuenta) q.set("cuenta", cuenta);
             if (desde) q.set("desde", desde);
             if (hasta) q.set("hasta", hasta);
             return q.toString();
           })()}`}
           className="ml-auto flex items-center gap-1.5 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700 shadow-sm transition-colors hover:bg-emerald-100">
          <Download size={15} /> Exportar a Excel
        </a>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          No se pudo leer el análisis: {error}
        </div>
      )}
      {cargando && !datos && (
        <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-8 text-sm text-slate-500 shadow-sm">
          <Loader2 size={16} className="animate-spin" /> Clasificando las ventas…
        </div>
      )}

      {datos && (
        <>
          <div className="grid grid-cols-3 gap-3">
            <Kpi label="Ventas del período" value={fMoney(t?.venta)}
                 pie={rangoActivo
                   ? `${(datos as Resp & { desde?: string }).desde ?? desde} → ${(datos as Resp & { hasta?: string }).hasta ?? hasta}`
                   : PERIODOS.find((p) => p.dias === dias)?.label} />
            <Kpi label="Unidades" value={fNum(t?.uds)} />
            <Kpi label="Categorías con venta" value={fNum(t?.categorias)}
                 pie={cuenta ? CUENTAS.find((c) => c.id === cuenta)?.label : "las 3 cuentas"} />
          </div>

          <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <table className="w-full table-fixed text-sm">
              {/* "Por cuenta" lleva 3 chips con cifras (~170 px): w-44 y con
                  flex-wrap — con menos, los chips se encimaban sobre
                  "Precio prom" (Eduardo, 31-jul). */}
              <colgroup>
                <col /><col className="w-24" /><col className="w-16" />
                <col className="w-20" /><col className="w-28" /><col className="w-32" />
                <col className="w-24" /><col className="w-44" />
              </colgroup>
              <thead>
                <tr className="border-b border-slate-200 bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500">
                  {/* "Pubs" abreviado a propósito: "PUBLICACIONES" + su "?" no
                      caben en 96 px y se encimaban sobre SKUS (Eduardo,
                      31-jul). El nombre completo va en el tooltip. */}
                  {([["cat", "Categoría", false], ["pub", "Pubs", true],
                     ["skus", "SKUs", true], ["uds", "Uds", true],
                     ["venta", "Ventas $", true], ["pct", "% del total", false],
                     ["prom", "Precio prom", true], ["cuentas", "Por cuenta", true],
                   ] as const).map(([id, label, right]) => (
                    <th key={id}
                        className={`px-3 py-2.5 font-semibold ${right ? "text-right" : "text-left"}`}>
                      {/* El buscador vive DENTRO de la cabecera de Categoría:
                          esa columna es flexible y su espacio sobrante quedaba
                          vacío (Eduardo, 31-jul). El input resetea el estilo
                          de th (uppercase/tracking) para leerse normal. */}
                      <span className={`flex items-center ${right ? "justify-end" : ""}`}>
                        <span className="inline-flex shrink-0 items-center">
                          {label}
                          <Ayuda titulo={AYUDA[id].titulo} texto={AYUDA[id].texto} />
                        </span>
                        {id === "cat" && (
                          <>
                            <span className="relative ml-4 min-w-0 flex-1" style={{ maxWidth: 300 }}>
                              <Search size={13} className="absolute left-2 top-1.5 text-slate-400" />
                              <input
                                value={q}
                                onChange={(e) => setQ(e.target.value)}
                                onClick={(e) => e.stopPropagation()}
                                placeholder="Buscar categoría…"
                                className="w-full rounded-lg border border-slate-200 bg-white py-1 pl-7 pr-2 text-xs font-normal normal-case tracking-normal text-slate-700 shadow-sm outline-none placeholder:text-slate-400 focus:border-indigo-400"
                              />
                            </span>
                            {/* La tabla solo lista lo que VENDIÓ en el período;
                                el buscador también alcanza lo oculto. Sin esta
                                nota, encontrar de pronto una categoría en $0
                                parecía un error y no una respuesta. */}
                            <span className="ml-2 hidden shrink-0 text-[10px] font-normal normal-case tracking-normal text-slate-400 lg:inline">
                              también encuentra categorías sin venta, ocultas de la tabla
                            </span>
                          </>
                        )}
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {arbol.map((nodo) => (
                  <FilaNodo key={nodo.clave} nodo={nodo} nivel={0}
                            ventaTotal={ventaTotal} maxPct={maxPct}
                            abiertas={abiertas} pubs={pubs} onToggle={alternar}
                            autoAbrir={buscando} />
                ))}
                {arbol.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-3 py-8 text-center text-sm text-slate-400">
                      {buscando
                        ? `Ninguna categoría coincide con “${q.trim()}” en este período.`
                        : "Sin ventas en el período seleccionado."}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <p className="text-center text-[11px] text-slate-400">
            Árbol completo de ML aplicado por SKU (las ventas de Amazon también se clasifican) ·
            expandir la última subcategoría muestra sus publicaciones · la fecha es la 1ª venta
            del período (la fecha de creación de la publicación no se conserva).
          </p>
        </>
      )}
    </div>
  );
}

function FilaNodo({ nodo, nivel, ventaTotal, maxPct, abiertas, pubs, onToggle, autoAbrir }: {
  nodo: Nodo; nivel: number; ventaTotal: number; maxPct: number;
  abiertas: Set<string>; pubs: Record<string, Pub[] | "cargando">;
  onToggle: (n: Nodo) => void; autoAbrir: boolean;
}) {
  // Buscando, las RAMAS se abren solas para mostrar dónde cayó la coincidencia;
  // las hojas no (sus publicaciones se piden al clic, y abrir 30 de golpe
  // sería una ráfaga de peticiones que nadie pidió).
  const abierta = abiertas.has(nodo.clave) || (autoAbrir && nodo.hijos.length > 0);
  const esHoja = nodo.hijos.length === 0;
  const pct = ventaTotal ? (nodo.venta / ventaTotal) * 100 : 0;
  const detalle = esHoja && nodo.category_id ? pubs[nodo.category_id] : undefined;
  const cuentas = [...nodo.cuentas.entries()].sort((a, b) => a[0].localeCompare(b[0]));

  return (
    <>
      <tr onClick={() => onToggle(nodo)}
          className={`cursor-pointer border-b border-slate-100 last:border-0 hover:bg-slate-50 ${
            nivel === 0 ? "" : "bg-slate-50/40"}`}>
        <td className="truncate px-3 py-2" style={{ paddingLeft: `${12 + nivel * 18}px` }}
            title={nodo.clave.replaceAll("›", " › ")}>
          <span className={`inline-flex items-center gap-1 ${
            nivel === 0 ? "font-medium text-slate-800" : "text-[13px] text-slate-600"}`}>
            <ChevronRight size={13}
                          className={`shrink-0 text-slate-400 transition-transform ${abierta ? "rotate-90" : ""}`} />
            {nodo.label}
          </span>
        </td>
        <td className="px-3 py-2 text-right tabular-nums text-slate-600"
            title={`${fNum(nodo.activas)} activas de ${fNum(nodo.publicaciones)} publicaciones vivas`}>
          {fNum(nodo.activas)} <span className="text-slate-400">/ {fNum(nodo.publicaciones)}</span>
        </td>
        <td className="px-3 py-2 text-right tabular-nums text-slate-600">{fNum(nodo.skus)}</td>
        <td className={`px-3 py-2 text-right font-semibold tabular-nums ${nodo.uds ? "text-slate-900" : "text-slate-300"}`}>{fNum(nodo.uds)}</td>
        <td className={`px-3 py-2 text-right font-semibold tabular-nums ${nodo.uds ? "text-emerald-700" : "text-slate-300"}`}
            title={nodo.uds ? undefined : "Con catálogo pero sin ventas en el período"}>{fMoney(nodo.venta)}</td>
        <td className="px-3 py-2">
          <div className="flex items-center gap-1.5">
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
              <div className={`h-full rounded-full ${nivel === 0 ? "bg-indigo-500/80" : "bg-slate-300"}`}
                   style={{ width: `${Math.min(100, (pct / maxPct) * 100)}%` }} />
            </div>
            <span className="w-10 text-right text-[11px] tabular-nums text-slate-600">
              {pct.toFixed(1)}%
            </span>
          </div>
        </td>
        <td className="px-3 py-2 text-right tabular-nums text-slate-600">
          {nodo.uds ? fMoney(nodo.venta / nodo.uds) : "—"}
        </td>
        <td className="px-3 py-2 text-right">
          <div className="flex flex-wrap justify-end gap-1">
            {cuentas.map(([cta, v]) => (
              <span key={cta}
                    title={`${cta}: ${fNum(v.uds)} uds · ${fMoney(v.venta)}`}
                    className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${CUENTA_CHIP[cta] ?? "bg-slate-100 text-slate-500"}`}>
                {CUENTA_INI[cta] ?? cta} {fNum(v.uds)}
              </span>
            ))}
          </div>
        </td>
      </tr>

      {abierta && !esHoja && nodo.hijos.map((h) => (
        <FilaNodo key={h.clave} nodo={h} nivel={nivel + 1}
                  ventaTotal={ventaTotal} maxPct={maxPct}
                  abiertas={abiertas} pubs={pubs} onToggle={onToggle}
                  autoAbrir={autoAbrir} />
      ))}

      {abierta && esHoja && detalle === "cargando" && (
        <tr><td colSpan={8} className="py-2 text-center text-[12px] text-slate-400"
                style={{ paddingLeft: `${30 + nivel * 18}px` }}>
          <Loader2 size={13} className="mr-1 inline animate-spin" /> Cargando publicaciones…
        </td></tr>
      )}
      {abierta && esHoja && Array.isArray(detalle) && detalle.map((p) => (
        <tr key={`${p.item_id}-${p.cuenta}-${p.sku}`}
            className="border-b border-slate-50 bg-white text-[12px] last:border-0">
          <td colSpan={8} className="py-1.5 pr-3"
              style={{ paddingLeft: `${30 + nivel * 18}px` }}>
            <div className="flex items-center gap-2 overflow-hidden">
              <span className={`shrink-0 rounded px-1 text-[9px] font-bold ${CUENTA_CHIP[p.cuenta] ?? "bg-slate-100 text-slate-500"}`}>
                {CUENTA_INI[p.cuenta] ?? p.cuenta}
              </span>
              <span className="shrink-0 font-mono text-[11px] text-indigo-600">
                {p.sku ?? "(sin SKU)"}
              </span>
              <span className="truncate text-slate-500" title={p.titulo ?? ""}>
                {p.titulo ?? "—"}
              </span>
              <span className="shrink-0 font-mono text-[10px] text-slate-400">{p.item_id ?? "—"}</span>
              {p.situacion && (
                <span className={`shrink-0 rounded px-1 py-0.5 text-[9px] font-bold uppercase ${SIT_CHIP[p.situacion.toLowerCase()] ?? "bg-slate-100 text-slate-500"}`}>
                  {p.situacion}
                </span>
              )}
              <span className="ml-auto shrink-0 tabular-nums text-slate-700">{fNum(p.uds)} uds</span>
              <span className="w-24 shrink-0 text-right font-semibold tabular-nums text-slate-800">
                {fMoney(p.venta)}
              </span>
              <span className="w-20 shrink-0 text-right tabular-nums text-slate-500"
                    title="Precio de lista actual del listado">
                {fMoney(p.precio)}
              </span>
              <span className="w-20 shrink-0 text-right text-[10px] tabular-nums text-slate-400"
                    title={`Primera venta del período: ${p.primera_venta} · última: ${p.ultima_venta}`}>
                {p.primera_venta}
              </span>
            </div>
          </td>
        </tr>
      ))}
      {abierta && esHoja && Array.isArray(detalle) && detalle.length === 0 && (
        <tr><td colSpan={8} className="py-2 text-[12px] text-slate-400"
                style={{ paddingLeft: `${30 + nivel * 18}px` }}>
          Sin publicaciones con venta en el período.
        </td></tr>
      )}
    </>
  );
}
