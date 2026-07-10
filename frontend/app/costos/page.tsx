"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Search,
  RotateCw,
  Calculator,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  Container,
  RefreshCw,
  Layers,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import Pagination from "@/components/Pagination";
import { listarCostos, contenedoresCosto, costoBulk } from "@/lib/api";
import type { CostoRow, ContenedorInfo, Paginacion, CostoBulkResp } from "@/lib/types";

const PER_PAGE = 50;
const COLOR = "#4F46E5";
const ACENTO = "#818CF8";
const TARIFA_CBM = 7500; // $/m³ (contenedor estándar) — igual que el backend
const DEFAULT_TC = 18.5; // tipo de cambio USD→MXN por defecto (editable)
const mxnToUsd = (v: number | null | undefined, tc: number) =>
  v == null ? "" : String(Math.round((v / (tc || DEFAULT_TC)) * 100) / 100);

function precioMXN(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 0 }).format(v);
}
const dims = (r: CostoRow) =>
  r.largo && r.ancho && r.alto ? `${r.largo}×${r.ancho}×${r.alto}` : "—";

// Edición inline por SKU (valores como string para inputs controlados).
type Edicion = { largo: string; ancho: string; alto: string; peso: string; costo_producto: string };
const s = (v: number | null | undefined) => (v == null ? "" : String(v));
const n = (v: string): number | null => (v.trim() ? Number(v) || null : null);
// costo_producto se edita en USD (guardado en MXN → se muestra ÷ TC).
const seedEdicion = (r: CostoRow, tc: number): Edicion => ({
  largo: s(r.largo), ancho: s(r.ancho), alto: s(r.alto), peso: s(r.peso),
  costo_producto: mxnToUsd(r.costo_producto, tc),
});

export default function CostosPage() {
  const [rows, setRows] = useState<CostoRow[]>([]);
  const [pag, setPag] = useState<Paginacion>({
    page: 1, per_page: PER_PAGE, total: 0, total_pages: 1, tiene_anterior: false, tiene_siguiente: false,
  });
  const [page, setPage] = useState(1);
  const [busquedaInput, setBusquedaInput] = useState("");
  const [busqueda, setBusqueda] = useState("");
  const [skusInput, setSkusInput] = useState("");
  const [skusFiltro, setSkusFiltro] = useState("");
  const [contenedor, setContenedor] = useState("");
  const [orden, setOrden] = useState("reciente");
  const [cargando, setCargando] = useState(true);
  const [contenedores, setContenedores] = useState<ContenedorInfo[]>([]);
  // Evita el flash de "Sin resultados" antes de que llegue la primera respuesta.
  const primeraCarga = useRef(true);

  const [seleccion, setSeleccion] = useState<Set<string>>(new Set());
  // Valores editados inline por SKU (se siembran al seleccionar la fila).
  const [ediciones, setEdiciones] = useState<Record<string, Edicion>>({});

  // Controles del bulk
  const [margenBulk, setMargenBulk] = useState("48");
  const [tcBulk, setTcBulk] = useState(String(DEFAULT_TC)); // tipo de cambio USD→MXN
  const [comisionBulk, setComisionBulk] = useState(""); // comisión ML % (vacío = ML/fallback)
  const [envioBulk, setEnvioBulk] = useState(true);
  const [bulkRun, setBulkRun] = useState(false);
  const [bulkResult, setBulkResult] = useState<CostoBulkResp | null>(null);
  const tcNum = () => Number(tcBulk) || DEFAULT_TC;

  const topRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    contenedoresCosto().then((r) => setContenedores(r.contenedores)).catch(() => {});
  }, []);

  useEffect(() => {
    const t = setTimeout(() => { setBusqueda(busquedaInput.trim()); setPage(1); }, 350);
    return () => clearTimeout(t);
  }, [busquedaInput]);

  useEffect(() => {
    const t = setTimeout(() => { setSkusFiltro(skusInput.trim()); setPage(1); }, 500);
    return () => clearTimeout(t);
  }, [skusInput]);

  const cargar = useCallback(() => {
    const ctrl = new AbortController();
    setCargando(true);
    listarCostos(
      {
        page, perPage: PER_PAGE, search: busqueda || undefined,
        skus: skusFiltro || undefined, contenedor: contenedor || undefined, orden,
      },
      ctrl.signal,
    )
      .then((r) => { setRows(r.items); setPag(r.paginacion); primeraCarga.current = false; })
      .catch((exc) => { if (exc?.name !== "AbortError") primeraCarga.current = false; })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [page, busqueda, skusFiltro, contenedor, orden]);

  useEffect(() => cargar(), [cargar]);

  function irPagina(p: number) {
    setPage(p);
    topRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function toggle(sku: string, row?: CostoRow) {
    setSeleccion((prev) => {
      const next = new Set(prev);
      if (next.has(sku)) next.delete(sku);
      else {
        next.add(sku);
        if (row) setEdiciones((e) => (e[sku] ? e : { ...e, [sku]: seedEdicion(row, tcNum()) }));
      }
      return next;
    });
  }
  const skusPagina = useMemo(() => rows.map((r) => r.sku), [rows]);
  const todosSel = skusPagina.length > 0 && skusPagina.every((k) => seleccion.has(k));
  function toggleTodos() {
    setSeleccion((prev) => {
      const next = new Set(prev);
      if (todosSel) skusPagina.forEach((k) => next.delete(k));
      else {
        rows.forEach((r) => {
          next.add(r.sku);
          setEdiciones((e) => (e[r.sku] ? e : { ...e, [r.sku]: seedEdicion(r, tcNum()) }));
        });
      }
      return next;
    });
  }

  const setEdicion = (sku: string, campo: keyof Edicion, valor: string) =>
    setEdiciones((e) => ({ ...e, [sku]: { ...(e[sku] ?? { largo: "", ancho: "", alto: "", peso: "", costo_producto: "" }), [campo]: valor } }));

  // Cálculo en vivo (CBM = vol×7500 MXN, costo = producto_MXN + CBM) mientras se
  // edita. costo_producto se ingresa en USD → se convierte a MXN con el TC.
  function vivo(r: CostoRow) {
    const ed = ediciones[r.sku];
    if (!seleccion.has(r.sku) || !ed) return { cbm: r.costo_cbm, costo: r.costo_unitario };
    const l = n(ed.largo), a = n(ed.ancho), h = n(ed.alto);
    const cpUsd = n(ed.costo_producto);
    const cpMxn = cpUsd != null ? cpUsd * tcNum() : null;
    const cbm = l && a && h ? Math.round((l * a * h) / 1_000_000 * TARIFA_CBM * 100) / 100 : r.costo_cbm;
    const costo = cpMxn != null && cbm != null ? Math.round((cpMxn + cbm) * 100) / 100 : r.costo_unitario;
    return { cbm, costo };
  }

  async function regenerarBulk() {
    if (seleccion.size === 0 || bulkRun) return;
    setBulkRun(true);
    setBulkResult(null);
    try {
      const tc = tcNum();
      const items = [...seleccion].map((sku) => {
        const ed = ediciones[sku] ?? ({} as Edicion);
        const cpUsd = n(ed.costo_producto ?? "");
        return {
          sku,
          costo_producto: cpUsd != null ? Math.round(cpUsd * tc * 100) / 100 : null, // USD→MXN
          largo: n(ed.largo ?? ""),
          ancho: n(ed.ancho ?? ""),
          alto: n(ed.alto ?? ""),
          peso: n(ed.peso ?? ""),
        };
      });
      const r = await costoBulk(items, {
        margen: (Number(margenBulk) || 0) / 100,
        pct_comision: comisionBulk.trim() ? (Number(comisionBulk) || 0) / 100 : null,
        incluir_envio: envioBulk,
        auto_cbm: true,
        sincronizar_woo: true,
      });
      setBulkResult(r);
      setSeleccion(new Set());
      setEdiciones({});
      cargar();
    } catch {
      setBulkResult({ ok: false, total: seleccion.size, exitosos: 0, resultados: [] });
    } finally {
      setBulkRun(false);
    }
  }

  return (
    <div className="min-h-screen">
      <AppNavbar />
      <main className="mx-auto max-w-[1600px] px-4 pb-32 pt-6 sm:px-6">
        {/* Banner */}
        <div ref={topRef} className="relative overflow-hidden rounded-3xl p-6 shadow-card"
          style={{ background: `linear-gradient(120deg, ${COLOR} 0%, ${ACENTO} 100%)`, color: "#FFF" }}>
          <div className="relative z-10 flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.2em] opacity-80">Centro Omnicanal · Costos</div>
              <h1 className="mt-1 flex items-center gap-2 text-3xl font-extrabold tracking-tight">
                <Calculator size={28} /> Costos
              </h1>
              <p className="mt-1 max-w-2xl text-sm opacity-90">
                Todos los SKUs con su costo por pieza. Edita medidas y costo inicial, regenera
                (CBM = volumen × $7.500/m³ → costo → precios) y guarda en la base + WooCommerce.
              </p>
            </div>
            <div className="text-right">
              <div className="text-4xl font-black tabular-nums">{new Intl.NumberFormat("es-MX").format(pag.total)}</div>
              <div className="text-xs font-semibold uppercase tracking-wide opacity-80">SKUs con costo</div>
            </div>
          </div>
        </div>

        {/* Filtros */}
        <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
          <div className="text-sm text-slate-500">
            {seleccion.size > 0
              ? <span><span className="font-bold text-indigo-600">{seleccion.size}</span> seleccionado(s)</span>
              : <span>Selecciona SKUs para regenerar en lote</span>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input value={busquedaInput} onChange={(e) => setBusquedaInput(e.target.value)} placeholder="SKU o nombre…"
                className="w-56 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 outline-none focus:ring-2" style={{ outlineColor: ACENTO }} />
            </div>
            <div className="relative">
              <Layers size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                value={skusInput}
                onChange={(e) => setSkusInput(e.target.value)}
                placeholder="Filtrar SKUs: TEC-0001, ORG-0885, caminadora…"
                title="Términos separados por coma: filtra y busca a la vez (SKU completo, parcial o palabra del nombre)"
                className="w-80 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 font-mono text-xs text-slate-700 outline-none transition-shadow placeholder:font-sans placeholder:text-sm placeholder:text-slate-400 focus:ring-2"
                style={{ outlineColor: ACENTO }}
              />
            </div>
            <div className="relative">
              <Container size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <select value={contenedor} onChange={(e) => { setContenedor(e.target.value); setPage(1); }}
                className="w-56 appearance-none rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 outline-none focus:ring-2" style={{ outlineColor: ACENTO }}>
                <option value="">Todos los contenedores</option>
                {contenedores.map((c) => (
                  <option key={c.contenedor} value={c.contenedor}>{c.contenedor} ({c.n})</option>
                ))}
              </select>
            </div>
            <select value={orden} onChange={(e) => { setOrden(e.target.value); setPage(1); }}
              className="rounded-lg border border-slate-200 bg-white py-2 px-3 text-sm text-slate-700 outline-none focus:ring-2" style={{ outlineColor: ACENTO }}>
              <option value="reciente">Más reciente</option>
              <option value="sku_asc">SKU A→Z</option>
              <option value="sku_desc">SKU Z→A</option>
              <option value="costo_desc">Costo ↓</option>
              <option value="costo_asc">Costo ↑</option>
              <option value="contenedor">Contenedor</option>
            </select>
            <button onClick={() => cargar()} title="Recargar"
              className="flex items-center justify-center rounded-lg border border-slate-200 bg-white p-2 text-slate-500 hover:bg-slate-50">
              <RotateCw size={16} className={cargando ? "animate-spin" : ""} />
            </button>
          </div>
        </div>

        {/* Resultado del bulk */}
        {bulkResult && (
          <div className="mt-4 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm">
            <div className="flex items-center gap-2 font-semibold text-slate-700">
              {bulkResult.exitosos === bulkResult.total
                ? <CheckCircle2 size={16} className="text-emerald-500" />
                : <AlertTriangle size={16} className="text-amber-500" />}
              Regeneración: {bulkResult.exitosos}/{bulkResult.total} OK
              <button onClick={() => setBulkResult(null)} className="ml-auto text-xs font-semibold text-slate-400 hover:text-slate-600">Ocultar</button>
            </div>
            {bulkResult.resultados.some((r) => !r.ok || r.aviso) && (
              <ul className="mt-2 space-y-0.5 text-xs text-slate-500">
                {bulkResult.resultados.filter((r) => !r.ok || r.aviso).slice(0, 12).map((r) => (
                  <li key={r.sku} className={r.ok ? "text-amber-600" : "text-red-600"}>
                    <span className="font-mono">{r.sku}</span> — {r.error ?? r.aviso}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        <div className="mt-4 rounded-xl border border-slate-200 bg-white px-4 py-3">
          <Pagination pag={pag} color={COLOR} textoColor="#FFF" onPage={irPagina} sincronizando={cargando} />
        </div>

        {/* Tabla */}
        <div className="mt-5 overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-card">
          <table className="w-full min-w-[1050px] text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-left text-[11px] uppercase tracking-wide text-slate-500">
                <th className="w-12 px-4 py-3">
                  <input type="checkbox" aria-label="Seleccionar todos" checked={todosSel} onChange={toggleTodos} className="h-4 w-4 cursor-pointer accent-indigo-600" />
                </th>
                <th className="px-4 py-3 font-semibold">SKU / Producto</th>
                <th className="px-3 py-3 font-semibold">Contenedor</th>
                <th className="px-3 py-3 font-semibold">Dimensiones (cm)</th>
                <th className="px-3 py-3 text-right font-semibold">Peso</th>
                <th className="px-3 py-3 text-right font-semibold">Costo prod. <span className="text-[9px] text-slate-400">(USD)</span></th>
                <th className="px-3 py-3 text-right font-semibold">Flete CBM</th>
                <th className="px-3 py-3 text-right font-semibold">Costo</th>
                <th className="px-3 py-3 text-right font-semibold">P. regular</th>
                <th className="px-3 py-3 text-right font-semibold">P. oferta</th>
              </tr>
            </thead>
            <tbody>
              {cargando || (rows.length === 0 && primeraCarga.current) ? (
                Array.from({ length: 10 }).map((_, i) => (
                  <tr key={i} className="border-b border-slate-100">
                    <td colSpan={10} className="px-4 py-4"><div className="h-5 w-full animate-pulse rounded bg-slate-100" /></td>
                  </tr>
                ))
              ) : rows.length === 0 ? (
                <tr><td colSpan={10} className="px-4 py-16 text-center text-slate-400">Sin resultados.</td></tr>
              ) : (
                rows.map((r) => {
                  const sel = seleccion.has(r.sku);
                  const ed = ediciones[r.sku];
                  const { cbm, costo } = vivo(r);
                  return (
                    <tr key={r.sku} className={["border-b border-slate-100 transition-colors", sel ? "bg-indigo-50/50" : "hover:bg-slate-50"].join(" ")}>
                      <td className="px-4 py-3">
                        <input type="checkbox" checked={sel} onChange={() => toggle(r.sku, r)} className="h-4 w-4 cursor-pointer accent-indigo-600" />
                      </td>
                      <td className="px-4 py-3">
                        <div className="font-mono text-xs text-slate-500">{r.sku}</div>
                        {r.nombre && <div className="line-clamp-1 max-w-[240px] text-xs text-slate-600">{r.nombre}</div>}
                      </td>
                      <td className="px-3 py-3 text-xs text-slate-500">{r.contenedor ?? "—"}</td>
                      {/* Dimensiones — editable al seleccionar */}
                      <td className="px-3 py-3 text-xs text-slate-600">
                        {sel && ed ? (
                          <div className="flex items-center gap-1">
                            <CeldaInput value={ed.largo} onChange={(v) => setEdicion(r.sku, "largo", v)} />
                            <span className="text-slate-300">×</span>
                            <CeldaInput value={ed.ancho} onChange={(v) => setEdicion(r.sku, "ancho", v)} />
                            <span className="text-slate-300">×</span>
                            <CeldaInput value={ed.alto} onChange={(v) => setEdicion(r.sku, "alto", v)} />
                          </div>
                        ) : (
                          <>
                            {dims(r)}
                            {r.volumen_m3 != null && <span className="ml-1 font-mono text-[10px] text-amber-600">{r.volumen_m3} m³</span>}
                          </>
                        )}
                      </td>
                      {/* Peso — editable */}
                      <td className="px-3 py-3 text-right text-slate-600">
                        {sel && ed ? <CeldaInput value={ed.peso} onChange={(v) => setEdicion(r.sku, "peso", v)} align="right" /> : (r.peso ?? "—")}
                      </td>
                      {/* Costo producto — editable en USD (guardado en MXN → ÷TC) */}
                      <td className="px-3 py-3 text-right text-slate-600">
                        {sel && ed
                          ? <CeldaInput value={ed.costo_producto} onChange={(v) => setEdicion(r.sku, "costo_producto", v)} align="right" prefijo="$" />
                          : (r.costo_producto != null ? `$${mxnToUsd(r.costo_producto, tcNum())}` : "—")}
                      </td>
                      {/* Flete CBM + Costo — en vivo si está seleccionado */}
                      <td className={["px-3 py-3 text-right", sel ? "font-semibold text-indigo-600" : "text-slate-600"].join(" ")}>{precioMXN(cbm)}</td>
                      <td className={["px-3 py-3 text-right font-semibold", sel ? "text-indigo-700" : "text-slate-800"].join(" ")}>{precioMXN(costo)}</td>
                      <td className="px-3 py-3 text-right text-slate-600">{precioMXN(r.precio_base)}</td>
                      <td className="px-3 py-3 text-right font-semibold text-slate-900">{precioMXN(r.precio_sugerido)}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <div className="mt-6 rounded-xl border border-slate-200 bg-white px-4 py-3">
          <Pagination pag={pag} color={COLOR} textoColor="#FFF" onPage={irPagina} sincronizando={cargando} />
        </div>
      </main>

      {/* Barra de acción: bulk */}
      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <div className="text-sm text-slate-500">
            <span className="font-bold text-indigo-600">{seleccion.size}</span> SKU(s) seleccionado(s)
            <span className="ml-2 text-xs text-slate-400">· edita medidas/costo en la fila, luego regenera (CBM=vol×7500) + precios y guarda en DB + Woo</span>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-500">
              TC USD→MXN
              <input value={tcBulk} onChange={(e) => setTcBulk(e.target.value)}
                className="w-16 rounded-lg border border-slate-200 px-2 py-1.5 text-sm text-slate-700 outline-none focus:ring-2" style={{ outlineColor: ACENTO }} />
            </label>
            <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-500">
              Margen %
              <input value={margenBulk} onChange={(e) => setMargenBulk(e.target.value)}
                className="w-16 rounded-lg border border-slate-200 px-2 py-1.5 text-sm text-slate-700 outline-none focus:ring-2" style={{ outlineColor: ACENTO }} />
            </label>
            <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-500" title="Vacío = comisión de ML (o estimada si no hay token)">
              Comisión %
              <input value={comisionBulk} onChange={(e) => setComisionBulk(e.target.value)} placeholder="auto"
                className="w-16 rounded-lg border border-slate-200 px-2 py-1.5 text-sm text-slate-700 outline-none focus:ring-2 placeholder:text-slate-300" style={{ outlineColor: ACENTO }} />
            </label>
            <label className="flex cursor-pointer items-center gap-1.5 text-xs font-semibold text-slate-500">
              <input type="checkbox" checked={envioBulk} onChange={(e) => setEnvioBulk(e.target.checked)} className="h-4 w-4 accent-indigo-600" />
              Sumar envío
            </label>
            <button onClick={regenerarBulk} disabled={seleccion.size === 0 || bulkRun}
              className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-bold text-white shadow-sm transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
              style={{ backgroundColor: COLOR }}>
              {bulkRun ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
              Regenerar y guardar ({seleccion.size})
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Input compacto para editar una celda de la tabla.
function CeldaInput({ value, onChange, align, prefijo }: {
  value: string; onChange: (v: string) => void; align?: "right"; prefijo?: string;
}) {
  return (
    <div className="relative inline-block">
      {prefijo && <span className="pointer-events-none absolute left-1.5 top-1/2 -translate-y-1/2 text-[11px] text-slate-400">{prefijo}</span>}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={[
          "w-16 rounded border border-indigo-200 bg-white py-1 text-xs text-slate-800 outline-none focus:ring-2",
          align === "right" ? "text-right" : "text-center",
          prefijo ? "pl-4 pr-1.5" : "px-1.5",
        ].join(" ")}
        style={{ outlineColor: ACENTO }}
      />
    </div>
  );
}
