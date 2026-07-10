"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Search, RotateCw, ImageIcon, Wand2, ChevronRight, Pencil, Layers, Loader2 } from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import Pagination from "@/components/Pagination";
import ChannelDots from "@/components/ChannelDots";
import ProductStudio from "@/components/ProductStudio";
import CostoEditor from "@/components/CostoEditor";
import { esPadre, TipoBadge, VariantesBoton, VariantesTabla } from "@/components/Variantes";

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
  const [skusInput, setSkusInput] = useState("");
  const [skusFiltro, setSkusFiltro] = useState("");
  const [cargando, setCargando] = useState(true);
  // Arranque en frío del backend: el índice de WooCommerce puede tardar varios
  // segundos en construirse. Mientras tanto, "0 resultados" no significa que
  // no haya productos — reintentamos en silencio en vez de mostrar vacío.
  const [preparando, setPreparando] = useState(false);
  const reintentos = useRef(0);
  // true hasta que la PRIMERA carga de esta página termine (con o sin filtro).
  // Evita que se vea "No se encontraron productos" en el instante antes de que
  // llegue la respuesta real, incluso si por alguna razón cargando ya es false.
  const primeraCarga = useRef(true);
  const [sel, setSel] = useState<Producto | null>(null);
  const [editCosto, setEditCosto] = useState<string | null>(null);
  // Padres con su lista de variantes desplegada (misma mecánica que Crear Productos)
  const [expandidos, setExpandidos] = useState<Set<string>>(new Set());

  function toggleExpandido(sku: string) {
    setExpandidos((prev) => {
      const s = new Set(prev);
      if (s.has(sku)) s.delete(sku);
      else s.add(sku);
      return s;
    });
  }

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

  useEffect(() => {
    const t = setTimeout(() => {
      setSkusFiltro(skusInput.trim());
      setPage(1);
    }, 500);
    return () => clearTimeout(t);
  }, [skusInput]);

  const cargar = useCallback(() => {
    const ctrl = new AbortController();
    setCargando(true);
    listarProductos(
      {
        canal: "general", page, perPage: PER_PAGE,
        search: busqueda || undefined, skus: skusFiltro || undefined,
      },
      ctrl.signal,
    )
      .then((r) => {
        setProductos(r.items);
        setPag(r.paginacion);
        // Sin búsqueda/filtro y 0 resultados → probablemente el índice de
        // WooCommerce todavía se está construyendo (arranque en frío). Reintenta
        // en vez de mostrar "no encontrados".
        if (!busqueda && !skusFiltro && r.paginacion.total === 0 && reintentos.current < 45) {
          reintentos.current += 1;
          setPreparando(true);
          setTimeout(() => cargar(), 1000);
          return;
        }
        reintentos.current = 0;
        setPreparando(false);
        primeraCarga.current = false;
      })
      .catch((exc) => {
        // El backend puede tardar en levantarse (deploy/reinicio) y rechazar la
        // conexión: reintentamos igual que con 0 resultados, en vez de dejar la
        // pantalla en "no encontrados" por un error de red silencioso.
        if (exc?.name === "AbortError") return;
        if (reintentos.current < 45) {
          reintentos.current += 1;
          setPreparando(true);
          setTimeout(() => cargar(), 1000);
        } else {
          primeraCarga.current = false;
        }
      })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [page, busqueda, skusFiltro]);

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
        <div className="mt-5 flex flex-wrap items-center justify-end gap-2">
          <div className="relative">
            <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              value={busquedaInput}
              onChange={(e) => setBusquedaInput(e.target.value)}
              placeholder="SKU o nombre…"
              className="w-64 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 outline-none placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-300"
            />
          </div>
          <div className="relative">
            <Layers size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              value={skusInput}
              onChange={(e) => setSkusInput(e.target.value)}
              placeholder="Filtrar SKUs: TEC-0001, ORG-0885, caminadora…"
              title="Términos separados por coma: filtra y busca a la vez (SKU completo, parcial o palabra del nombre)"
              className="w-80 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 font-mono text-xs text-slate-700 outline-none transition-shadow placeholder:font-sans placeholder:text-sm placeholder:text-slate-400 focus:ring-2 focus:ring-indigo-300"
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
          {/* Encabezado de columnas (alineado con las filas de abajo) */}
          <div className="hidden items-center gap-4 border-b border-slate-200 bg-slate-50 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500 md:flex">
            <span className="min-w-0 flex-1">Producto</span>
            <span className="w-20 shrink-0 text-center">Tipo</span>
            <span className="w-36 shrink-0 text-center">Variantes</span>
            <span className="flex shrink-0 items-center gap-4">
              <span className="hidden w-16 text-center sm:block">Canales</span>
              <span className="w-24 text-right">Precio</span>
            </span>
            <span className="w-8 shrink-0" />
          </div>

          {cargando || (productos.length === 0 && primeraCarga.current) ? (
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
              {preparando ? (
                <span className="inline-flex items-center gap-2">
                  <Loader2 size={16} className="animate-spin" />
                  Preparando el catálogo de WooCommerce…
                </span>
              ) : (
                "No se encontraron productos."
              )}
            </div>
          ) : (
            productos.map((p) => {
              const padre = esPadre(p);
              const abierto = padre && expandidos.has(p.sku);
              return (
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
                  </button>

                  {/* Columna TIPO */}
                  <div className="hidden w-20 shrink-0 justify-center md:flex">
                    <TipoBadge padre={padre} onClick={() => setSel(p)} />
                  </div>

                  {/* Columna VARIANTES */}
                  <div className="hidden w-36 shrink-0 justify-center md:flex">
                    {padre ? (
                      <VariantesBoton
                        n={p.variantes.length}
                        abierto={abierto}
                        onClick={() => toggleExpandido(p.sku)}
                      />
                    ) : (
                      <span className="text-xs text-slate-300">—</span>
                    )}
                  </div>

                  {/* Canales + precio (también abren el Estudio) */}
                  <button
                    onClick={() => setSel(p)}
                    className="flex shrink-0 items-center gap-4 text-left"
                  >
                    <div className="hidden w-16 shrink-0 justify-center sm:flex">
                      <ChannelDots canales={p.canales} colorMap={colorMap} labelMap={labelMap} />
                    </div>
                    <div className="w-24 shrink-0 text-right">
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

                {/* Variantes del padre (desplegable) */}
                {abierto && (
                  <div className="bg-violet-50/40 px-4 pb-4 pt-1">
                    <VariantesTabla
                      variantes={p.variantes}
                      colorMap={colorMap}
                      labelMap={labelMap}
                    />
                  </div>
                )}

                {/* Panel de costos que se despliega inline */}
                {editCosto === p.sku && (
                  <div className="bg-slate-50/60 px-4 pb-4 pt-1">
                    <CostoEditor sku={p.sku} nombre={p.nombre} onClose={() => setEditCosto(null)} onGuardado={cargar} />
                  </div>
                )}
              </div>
              );
            })
          )}
        </div>

        {/* Paginación inferior */}
        <div className="mt-6 rounded-xl border border-slate-200 bg-white px-4 py-3">
          <Pagination pag={pag} color={INDIGO} textoColor="#fff" onPage={irPagina} />
        </div>
      </main>

      {/* Estudio de producto (overlay) */}
      <ProductStudio sku={sel?.sku ?? null} producto={sel} canales={canales} onClose={() => setSel(null)} onGuardado={cargar} />
    </div>
  );
}
