"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Search, RotateCw, ImageIcon, Wand2, ChevronRight, Pencil } from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import Pagination from "@/components/Pagination";
import ChannelDots from "@/components/ChannelDots";
import ProductStudio from "@/components/ProductStudio";
import CostoEditor from "@/components/CostoEditor";

import { listarCanales, listarProductos } from "@/lib/api";
import type { CanalInfo, Paginacion, Producto } from "@/lib/types";

const PER_PAGE = 40;
const INDIGO = "#4F46E5";

function precioMXN(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: "MXN",
  }).format(v);
}

export default function ProductosPage() {
  const [canales, setCanales] = useState<CanalInfo[]>([]);
  const [productos, setProductos] = useState<Producto[]>([]);
  const [pag, setPag] = useState<Paginacion>({
    page: 1, per_page: PER_PAGE, total: 0, total_pages: 1,
    tiene_anterior: false, tiene_siguiente: false,
  });
  const [page, setPage] = useState(1);
  const [busquedaInput, setBusquedaInput] = useState("");
  const [busqueda, setBusqueda] = useState("");
  const [cargando, setCargando] = useState(true);
  const [sel, setSel] = useState<Producto | null>(null);
  const [editCosto, setEditCosto] = useState<string | null>(null);

  const topRef = useRef<HTMLDivElement>(null);

  const colorMap = Object.fromEntries(canales.map((c) => [c.id, c.color]));
  const labelMap = Object.fromEntries(canales.map((c) => [c.id, c.label]));

  useEffect(() => {
    listarCanales().then(setCanales).catch(() => setCanales([]));
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      setBusqueda(busquedaInput.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(t);
  }, [busquedaInput]);

  const cargar = useCallback(() => {
    const ctrl = new AbortController();
    setCargando(true);
    listarProductos(
      { canal: "general", page, perPage: PER_PAGE, search: busqueda || undefined },
      ctrl.signal,
    )
      .then((r) => {
        setProductos(r.items);
        setPag(r.paginacion);
      })
      .catch(() => {})
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [page, busqueda]);

  useEffect(() => cargar(), [cargar]);

  function irPagina(p: number) {
    setPage(p);
    topRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />

      <main className="mx-auto max-w-[1400px] px-4 pb-16 pt-6 sm:px-6">
        {/* Encabezado */}
        <div
          ref={topRef}
          className="relative overflow-hidden rounded-3xl p-6 text-white shadow-card"
          style={{ background: `linear-gradient(120deg, ${INDIGO} 0%, #7C6CF0 100%)` }}
        >
          <div className="relative z-10 flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.2em] opacity-80">
                <Wand2 size={14} /> Estudio de producto
              </div>
              <h1 className="mt-1 text-3xl font-extrabold tracking-tight">Productos</h1>
              <p className="mt-1 max-w-xl text-sm opacity-90">
                Tu catálogo de WooCommerce. Haz clic en un producto para ver su ficha completa y
                generar contenido optimizado por canal con IA.
              </p>
            </div>
            <div className="text-right">
              <div className="text-4xl font-black tabular-nums">
                {new Intl.NumberFormat("es-MX").format(pag.total)}
              </div>
              <div className="text-xs font-semibold uppercase tracking-wide opacity-80">
                productos
              </div>
            </div>
          </div>
          <div className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full bg-white/20" />
        </div>

        {/* Buscador */}
        <div className="mt-5 flex items-center justify-end gap-2">
          <div className="relative">
            <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              value={busquedaInput}
              onChange={(e) => setBusquedaInput(e.target.value)}
              placeholder="SKU o nombre…"
              className="w-64 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-300"
            />
          </div>
          <button
            onClick={cargar}
            title="Recargar"
            className="flex items-center justify-center rounded-lg border border-slate-200 bg-white p-2 text-slate-500 transition-colors hover:bg-slate-50"
          >
            <RotateCw size={16} className={cargando ? "animate-spin" : ""} />
          </button>
        </div>

        {/* Paginación superior */}
        <div className="mt-4 rounded-xl border border-slate-200 bg-white px-4 py-3">
          <Pagination pag={pag} color={INDIGO} textoColor="#fff" onPage={irPagina} />
        </div>

        {/* Lista */}
        <div className="mt-5 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
          {cargando ? (
            Array.from({ length: 10 }).map((_, i) => (
              <div key={i} className="flex animate-pulse items-center gap-4 border-b border-slate-100 px-4 py-4">
                <div className="h-16 w-16 rounded-lg bg-slate-100" />
                <div className="flex-1 space-y-2">
                  <div className="h-4 w-1/2 rounded bg-slate-100" />
                  <div className="h-3 w-3/4 rounded bg-slate-100" />
                </div>
                <div className="h-5 w-20 rounded bg-slate-100" />
              </div>
            ))
          ) : productos.length === 0 ? (
            <div className="px-4 py-16 text-center text-sm text-slate-400">
              No se encontraron productos.
            </div>
          ) : (
            productos.map((p) => (
              <div key={p.sku} className="border-b border-slate-100">
                <div className="flex items-center gap-4 px-4 py-3.5 transition-colors hover:bg-indigo-50/40">
                  {/* Zona clickeable: abre el Estudio */}
                  <button
                    onClick={() => setSel(p)}
                    className="flex min-w-0 flex-1 items-center gap-4 text-left"
                  >
                    {/* Imagen */}
                    <div className="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-slate-100 bg-slate-50">
                      {p.imagen ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={p.imagen} alt="" loading="lazy" className="h-full w-full object-contain" />
                      ) : (
                        <ImageIcon size={22} className="text-slate-300" />
                      )}
                    </div>

                    {/* Título + descripción + categoría */}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate font-semibold text-slate-800">{p.nombre}</span>
                        <span className="shrink-0 rounded bg-slate-100 px-1.5 font-mono text-[10px] text-slate-400">
                          {p.sku}
                        </span>
                      </div>
                      {p.descripcion_corta && (
                        <p className="mt-0.5 line-clamp-1 text-xs text-slate-500">{p.descripcion_corta}</p>
                      )}
                      {p.categoria_path.length > 0 && (
                        <div className="mt-1 flex items-center gap-1 text-[11px] text-slate-400">
                          {p.categoria_path.map((c, i) => (
                            <span key={i} className="flex items-center gap-1">
                              {i > 0 && <ChevronRight size={10} />}
                              {c.nombre}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Canales presentes */}
                    <div className="hidden shrink-0 sm:block">
                      <ChannelDots canales={p.canales} colorMap={colorMap} labelMap={labelMap} />
                    </div>

                    {/* Precio */}
                    <div className="shrink-0 text-right">
                      <div className="font-bold text-slate-900">{precioMXN(p.precio)}</div>
                      <div className="text-[11px] text-slate-400">{p.stock ?? "—"} en stock</div>
                    </div>
                  </button>

                  {/* Editar costo — icono de lápiz junto al precio (sin modal) */}
                  <button
                    onClick={() => setEditCosto((s) => (s === p.sku ? null : p.sku))}
                    title="Editar costo y precios"
                    className={[
                      "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border transition-colors",
                      editCosto === p.sku
                        ? "border-indigo-300 bg-indigo-50 text-indigo-700"
                        : "border-slate-200 bg-white text-slate-400 hover:bg-slate-50 hover:text-indigo-600",
                    ].join(" ")}
                  >
                    <Pencil size={15} />
                  </button>
                </div>

                {/* Panel de costos que se despliega inline */}
                {editCosto === p.sku && (
                  <div className="bg-slate-50/60 px-4 pb-4 pt-1">
                    <CostoEditor sku={p.sku} nombre={p.nombre} onClose={() => setEditCosto(null)} />
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        {/* Paginación inferior */}
        <div className="mt-6 rounded-xl border border-slate-200 bg-white px-4 py-3">
          <Pagination pag={pag} color={INDIGO} textoColor="#fff" onPage={irPagina} />
        </div>
      </main>

      {/* Estudio de producto (overlay) */}
      <ProductStudio sku={sel?.sku ?? null} producto={sel} canales={canales} onClose={() => setSel(null)} />
    </div>
  );
}
