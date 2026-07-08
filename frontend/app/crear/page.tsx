"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Search,
  RotateCw,
  ImageIcon,
  PackagePlus,
  Link2,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  Database,
  Layers,
  ChevronDown,
  ChevronRight,
  Eye,
  X,
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
  Container,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import Pagination from "@/components/Pagination";
import {
  listarCandidatos,
  crearProductos,
  sincronizarDrafts,
  progresoCreacion,
  categoriasDisponibles,
  type ProgresoCreacionItem,
} from "@/lib/api";
import type { Paginacion, Producto } from "@/lib/types";

const PER_PAGE = 50;
const COLOR = "#4F46E5"; // índigo Kubera
const ACENTO = "#818CF8";

function precioMXN(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: "MXN",
    maximumFractionDigits: 0,
  }).format(v);
}

function esAlibaba(url: string): boolean {
  const u = url.trim().toLowerCase();
  return /^https?:\/\/.+alibaba\.com/.test(u) || /^https?:\/\/.+\.1688\.com/.test(u);
}

interface Resultado {
  tipo: "ok" | "error";
  texto: string;
}

export default function CrearProductosPage() {
  const [productos, setProductos] = useState<Producto[]>([]);
  const [pag, setPag] = useState<Paginacion>({
    page: 1, per_page: PER_PAGE, total: 0, total_pages: 1,
    tiene_anterior: false, tiene_siguiente: false,
  });
  const [page, setPage] = useState(1);
  const [busquedaInput, setBusquedaInput] = useState("");
  const [busqueda, setBusqueda] = useState("");
  // Filtro multi-SKU: pega muchos SKUs separados por coma y solo esos se muestran
  const [skusInput, setSkusInput] = useState("");
  const [skusFiltro, setSkusFiltro] = useState("");
  // Buscador por categoría (nombre parcial) con autocompletado
  const [categoriaInput, setCategoriaInput] = useState("");
  const [categoriaFiltro, setCategoriaFiltro] = useState("");
  const [categoriasLista, setCategoriasLista] = useState<string[]>([]);

  useEffect(() => {
    categoriasDisponibles()
      .then((r) => setCategoriasLista(r.categorias))
      .catch(() => {});
  }, []);
  // Orden por columna: valor|costo|stock|tipo + _asc|_desc
  const [orden, setOrden] = useState("valor_desc");
  const [cargando, setCargando] = useState(true);

  function toggleOrden(campo: string) {
    setOrden((prev) =>
      prev === `${campo}_desc` ? `${campo}_asc` : `${campo}_desc`,
    );
    setPage(1);
  }

  function IconoOrden({ campo }: { campo: string }) {
    if (orden === `${campo}_desc`) return <ArrowDown size={12} className="inline" />;
    if (orden === `${campo}_asc`) return <ArrowUp size={12} className="inline" />;
    return <ArrowUpDown size={12} className="inline opacity-40" />;
  }

  // Modal de imagen grande (botón de ojo sobre la miniatura)
  const [imagenModal, setImagenModal] = useState<{ src: string; nombre: string } | null>(null);

  // Padres (variables) con su lista de variantes desplegada
  const [expandidos, setExpandidos] = useState<Set<string>>(new Set());

  function toggleExpandido(sku: string) {
    setExpandidos((prev) => {
      const next = new Set(prev);
      next.has(sku) ? next.delete(sku) : next.add(sku);
      return next;
    });
  }

  // Selección masiva + URL de Alibaba por SKU (persiste entre páginas)
  const [seleccion, setSeleccion] = useState<Set<string>>(new Set());
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [enviando, setEnviando] = useState(false);
  const [resultado, setResultado] = useState<Resultado | null>(null);

  const topRef = useRef<HTMLDivElement>(null);

  // ── Debounce de búsqueda ────────────────────────────────────────────
  useEffect(() => {
    const t = setTimeout(() => {
      setBusqueda(busquedaInput.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(t);
  }, [busquedaInput]);

  // ── Debounce del filtro multi-SKU ───────────────────────────────────
  useEffect(() => {
    const t = setTimeout(() => {
      setSkusFiltro(skusInput.trim());
      setPage(1);
    }, 500);
    return () => clearTimeout(t);
  }, [skusInput]);

  // ── Debounce del buscador de categoría ──────────────────────────────
  useEffect(() => {
    const t = setTimeout(() => {
      setCategoriaFiltro(categoriaInput.trim());
      setPage(1);
    }, 350);
    return () => clearTimeout(t);
  }, [categoriaInput]);

  // ── Carga de candidatos ─────────────────────────────────────────────
  // El backend construye el índice de WooCommerce en caché; justo tras un
  // reinicio puede tardar unos segundos y devolver total=0. En ese caso
  // reintentamos automáticamente unas cuantas veces (ventana de calentamiento).
  const [preparando, setPreparando] = useState(false);
  // false mientras el backend sigue construyendo el índice (carga progresiva)
  const [indiceCompleto, setIndiceCompleto] = useState(true);
  const reintentos = useRef(0);

  const cargar = useCallback((silencioso = false) => {
    const ctrl = new AbortController();
    if (!silencioso) setCargando(true);
    listarCandidatos(
      {
        page,
        perPage: PER_PAGE,
        search: busqueda || undefined,
        skus: skusFiltro || undefined,
        orden,
        categoria: categoriaFiltro || undefined,
      },
      ctrl.signal,
    )
      .then((r) => {
        setProductos(r.items);
        setPag(r.paginacion);
        setIndiceCompleto(r.completo !== false);
        // Índice construyéndose (carga progresiva): recargar en silencio para
        // que el total y el orden se vayan actualizando sin parpadeos.
        if (r.completo === false) {
          setPreparando(r.paginacion.total === 0);
          setTimeout(() => cargar(true), 4000);
          return;
        }
        // Sin búsqueda/filtro y 0 resultados → índice aún vacío; reintentar.
        if (!busqueda && !skusFiltro && r.paginacion.total === 0 && reintentos.current < 45) {
          reintentos.current += 1;
          setPreparando(true);
          setTimeout(() => cargar(), 5000);
        } else {
          reintentos.current = 0;
          setPreparando(false);
        }
      })
      .catch(() => {})
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [page, busqueda, skusFiltro, orden, categoriaFiltro]);

  useEffect(() => cargar(), [cargar]);

  // ── Sincronizar Odoo → Woo (crea drafts de los SKUs faltantes) ──────
  const [sincronizando, setSincronizando] = useState(false);

  async function sincronizarOdoo() {
    if (sincronizando) return;
    setSincronizando(true);
    setResultado(null);
    try {
      const r = await sincronizarDrafts(100);
      const partes = [
        r.mensaje ?? `${r.creados.length} draft(s) creados en WooCommerce`,
      ];
      if (!r.mensaje && r.faltantes_restantes > 0) {
        partes.push(`quedan ${r.faltantes_restantes} por crear (vuelve a sincronizar)`);
      }
      if (r.errores.length > 0) partes.push(`${r.errores.length} error(es)`);
      setResultado({ tipo: r.errores.length ? "error" : "ok", texto: partes.join(" · ") });
      cargar();
    } catch (e) {
      setResultado({
        tipo: "error",
        texto: e instanceof Error ? e.message : "La sincronización con Odoo falló.",
      });
    } finally {
      setSincronizando(false);
    }
  }

  function irPagina(p: number) {
    setPage(p);
    topRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // ── Selección ───────────────────────────────────────────────────────
  function toggle(sku: string) {
    setSeleccion((prev) => {
      const next = new Set(prev);
      next.has(sku) ? next.delete(sku) : next.add(sku);
      return next;
    });
  }

  const skusPagina = useMemo(() => productos.map((p) => p.sku), [productos]);
  const todosSelPagina =
    skusPagina.length > 0 && skusPagina.every((s) => seleccion.has(s));

  function toggleTodosPagina() {
    setSeleccion((prev) => {
      const next = new Set(prev);
      if (todosSelPagina) skusPagina.forEach((s) => next.delete(s));
      else skusPagina.forEach((s) => next.add(s));
      return next;
    });
  }

  function setUrl(sku: string, valor: string) {
    setUrls((prev) => ({ ...prev, [sku]: valor }));
  }

  // ── Progreso de creación (polling mientras haya productos en proceso) ─
  const [progreso, setProgreso] = useState<ProgresoCreacionItem[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const iniciarPolling = useCallback(() => {
    if (pollRef.current) return;
    pollRef.current = setInterval(async () => {
      try {
        const r = await progresoCreacion();
        setProgreso(r.items);
        const activos = r.items.some(
          (i) => i.estado === "en_cola" || i.estado === "procesando",
        );
        if (!activos && pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
          cargar(); // los completados salen de Crear Productos
        }
      } catch {
        /* siguiente tick */
      }
    }, 5000);
  }, [cargar]);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  // Al abrir/recargar la vista: recuperar la cola de creación en curso
  useEffect(() => {
    progresoCreacion()
      .then((r) => {
        if (r.items.length) {
          setProgreso(r.items);
          if (r.items.some((i) => i.estado === "en_cola" || i.estado === "procesando")) {
            iniciarPolling();
          }
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Validación / envío ──────────────────────────────────────────────
  // Nota: la selección puede incluir SKUs de otras páginas ya no cargados.
  const totalSel = seleccion.size;
  const selSinUrl = useMemo(
    () => [...seleccion].filter((s) => !esAlibaba(urls[s] ?? "")),
    [seleccion, urls],
  );
  const puedeCrear = totalSel > 0 && selSinUrl.length === 0 && !enviando;

  async function enviar() {
    setResultado(null);
    if (totalSel === 0) return;
    if (selSinUrl.length > 0) {
      setResultado({
        tipo: "error",
        texto: `Faltan URLs de Alibaba válidas en ${selSinUrl.length} producto(s) seleccionado(s).`,
      });
      return;
    }
    const items = [...seleccion].map((sku) => {
      const p = productos.find((x) => x.sku === sku);
      return { sku, wc_id: p?.wc_id ?? null, alibaba_url: (urls[sku] ?? "").trim() };
    });
    setEnviando(true);
    try {
      const r = await crearProductos(items);
      setResultado({
        tipo: "ok",
        texto:
          r.mensaje ??
          `Se enviaron ${r.recibidos} producto(s) a crear.`,
      });
      setSeleccion(new Set());
      setProgreso(
        items.map((i) => ({ sku: i.sku, estado: "en_cola", paso: "En cola…" })),
      );
      iniciarPolling();
    } catch (e) {
      setResultado({
        tipo: "error",
        texto: e instanceof Error ? e.message : "No se pudo enviar la solicitud.",
      });
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="min-h-screen">
      <AppNavbar />

      <main className="mx-auto max-w-[1600px] px-4 pb-32 pt-6 sm:px-6">
        {/* Banner */}
        <div
          ref={topRef}
          className="relative overflow-hidden rounded-3xl p-6 shadow-card"
          style={{
            background: `linear-gradient(120deg, ${COLOR} 0%, ${ACENTO} 100%)`,
            color: "#FFFFFF",
          }}
        >
          <div className="relative z-10 flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.2em] opacity-80">
                Centro Omnicanal · Alta de productos
              </div>
              <h1 className="mt-1 flex items-center gap-2 text-3xl font-extrabold tracking-tight">
                <PackagePlus size={28} /> Crear Productos
              </h1>
              <p className="mt-1 max-w-2xl text-sm opacity-90">
                Productos que están en Odoo pero aún no están listos ni publicados en
                WooCommerce. Selecciónalos, pega la URL de Alibaba de cada uno y
                mándalos a crear.
              </p>
            </div>
            <div className="text-right">
              <div className="text-4xl font-black tabular-nums">
                {new Intl.NumberFormat("es-MX").format(pag.total)}
              </div>
              <div className="text-xs font-semibold uppercase tracking-wide opacity-80">
                por crear
              </div>
            </div>
          </div>
          <div
            className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full opacity-20"
            style={{ background: "#FFFFFF" }}
          />
        </div>

        {/* Panel de progreso de creación */}
        {progreso.length > 0 && (
          <div className="mt-6 rounded-2xl border border-indigo-100 bg-white p-4 shadow-card">
            <div className="mb-3 flex items-center gap-2 text-sm font-bold text-indigo-700">
              {progreso.some((p) => p.estado === "en_cola" || p.estado === "procesando") ? (
                <Loader2 size={15} className="animate-spin" />
              ) : (
                <CheckCircle2 size={15} className="text-emerald-500" />
              )}
              Creación de productos
              <button
                onClick={() => setProgreso([])}
                className="ml-auto text-xs font-semibold text-slate-400 hover:text-slate-600"
              >
                Ocultar
              </button>
            </div>
            <div className="space-y-1.5">
              {progreso.map((p) => (
                <div key={p.sku} className="flex items-center gap-2 text-sm">
                  {p.estado === "completado" ? (
                    <CheckCircle2 size={14} className="shrink-0 text-emerald-500" />
                  ) : p.estado === "error" ? (
                    <AlertTriangle size={14} className="shrink-0 text-red-500" />
                  ) : (
                    <Loader2 size={14} className="shrink-0 animate-spin text-indigo-500" />
                  )}
                  <span className="font-mono text-xs text-slate-500">{p.sku}</span>
                  <span
                    className={[
                      "truncate",
                      p.estado === "error" ? "text-red-600" : "text-slate-600",
                    ].join(" ")}
                  >
                    {p.titulo ? `${p.titulo} · ` : ""}
                    {p.paso}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Barra: búsqueda + refrescar + contador de selección */}
        <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
          <div className="text-sm text-slate-500">
            {totalSel > 0 ? (
              <span>
                <span className="font-bold text-indigo-600">{totalSel}</span>{" "}
                seleccionado(s)
                {selSinUrl.length > 0 && (
                  <span className="ml-2 text-amber-600">
                    · {selSinUrl.length} sin URL de Alibaba
                  </span>
                )}
              </span>
            ) : (
              <span>Selecciona productos para crear</span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="relative">
              <Search
                size={16}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <input
                value={busquedaInput}
                onChange={(e) => setBusquedaInput(e.target.value)}
                placeholder="SKU o nombre…"
                className="w-56 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 outline-none transition-shadow placeholder:text-slate-400 focus:ring-2"
                style={{ outlineColor: ACENTO }}
              />
            </div>
            <div className="relative">
              <Search
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <input
                value={categoriaInput}
                onChange={(e) => setCategoriaInput(e.target.value)}
                placeholder="Categoría…"
                title="Filtra por nombre de categoría (parcial) — con autocompletado"
                list="categorias-disponibles"
                className="w-44 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 outline-none transition-shadow placeholder:text-slate-400 focus:ring-2"
                style={{ outlineColor: ACENTO }}
              />
              <datalist id="categorias-disponibles">
                {categoriasLista.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </div>
            <div className="relative">
              <Layers
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <input
                value={skusInput}
                onChange={(e) => setSkusInput(e.target.value)}
                placeholder="Filtrar SKUs: TEC-0001, ORG-0885, caminadora…"
                title="Términos separados por coma: filtra y busca a la vez (SKU completo, parcial o palabra del nombre)"
                className="w-80 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 font-mono text-xs text-slate-700 outline-none transition-shadow placeholder:font-sans placeholder:text-sm placeholder:text-slate-400 focus:ring-2"
                style={{ outlineColor: ACENTO }}
              />
            </div>
            <button
              onClick={() => cargar()}
              title="Recargar"
              className="flex items-center justify-center rounded-lg border border-slate-200 bg-white p-2 text-slate-500 transition-colors hover:bg-slate-50"
            >
              <RotateCw size={16} className={cargando ? "animate-spin" : ""} />
            </button>
            <button
              onClick={sincronizarOdoo}
              disabled={sincronizando}
              title="Crea como borrador (draft) en WooCommerce los SKUs que están en Odoo y faltan en Woo"
              className="flex items-center gap-2 rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-2 text-sm font-semibold text-indigo-700 transition-colors hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {sincronizando ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Database size={16} />
              )}
              {sincronizando ? "Sincronizando…" : "Sincronizar Odoo"}
            </button>
          </div>
        </div>

        {/* Paginación superior */}
        <div className="mt-4 rounded-xl border border-slate-200 bg-white px-4 py-3">
          <Pagination
            pag={pag}
            color={COLOR}
            textoColor="#FFFFFF"
            onPage={irPagina}
            sincronizando={!indiceCompleto || cargando}
          />
        </div>

        {/* Tabla de candidatos */}
        <div className="mt-5 overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-card">
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className="border-b border-slate-200 bg-slate-50 text-left text-[11px] uppercase tracking-wide text-slate-500">
                <th className="w-12 px-4 py-3">
                  <input
                    type="checkbox"
                    aria-label="Seleccionar todos"
                    checked={todosSelPagina}
                    onChange={toggleTodosPagina}
                    className="h-4 w-4 cursor-pointer accent-indigo-600"
                  />
                </th>
                <th className="px-4 py-3 font-semibold">Producto</th>
                <th className="px-3 py-3 text-center font-semibold">
                  <button onClick={() => toggleOrden("tipo")} className="inline-flex items-center gap-1 uppercase hover:text-slate-700">
                    Tipo <IconoOrden campo="tipo" />
                  </button>
                </th>
                <th className="px-3 py-3 text-center font-semibold">Variantes</th>
                <th className="px-3 py-3 font-semibold">Categoría</th>
                <th className="px-3 py-3 text-right font-semibold">
                  <button onClick={() => toggleOrden("costo")} className="inline-flex items-center gap-1 uppercase hover:text-slate-700">
                    Costo <IconoOrden campo="costo" />
                  </button>
                </th>
                <th className="px-3 py-3 text-center font-semibold">
                  <button onClick={() => toggleOrden("stock")} className="inline-flex items-center gap-1 uppercase hover:text-slate-700">
                    Stock <IconoOrden campo="stock" />
                  </button>
                </th>
                <th className="px-3 py-3 text-right font-semibold">
                  <button onClick={() => toggleOrden("valor")} className="inline-flex items-center gap-1 uppercase hover:text-slate-700">
                    Valor <IconoOrden campo="valor" />
                  </button>
                </th>
                <th className="px-3 py-3 font-semibold">Contenedor</th>
                <th className="min-w-[360px] px-4 py-3 font-semibold">URL de Alibaba</th>
              </tr>
            </thead>
            <tbody>
              {cargando ? (
                Array.from({ length: 10 }).map((_, i) => (
                  <tr key={i} className="border-b border-slate-100">
                    <td className="px-4 py-4">
                      <div className="h-4 w-4 animate-pulse rounded bg-slate-100" />
                    </td>
                    <td className="px-4 py-4">
                      <div className="flex items-center gap-3">
                        <div className="h-20 w-20 animate-pulse rounded-lg bg-slate-100" />
                        <div className="h-4 w-48 animate-pulse rounded bg-slate-100" />
                      </div>
                    </td>
                    <td className="px-3 py-3"><div className="mx-auto h-4 w-14 animate-pulse rounded bg-slate-100" /></td>
                    <td className="px-3 py-3"><div className="mx-auto h-4 w-20 animate-pulse rounded bg-slate-100" /></td>
                    <td className="px-3 py-3"><div className="h-4 w-20 animate-pulse rounded bg-slate-100" /></td>
                    <td className="px-3 py-3"><div className="ml-auto h-4 w-16 animate-pulse rounded bg-slate-100" /></td>
                    <td className="px-3 py-3"><div className="mx-auto h-4 w-10 animate-pulse rounded bg-slate-100" /></td>
                    <td className="px-3 py-3"><div className="ml-auto h-4 w-16 animate-pulse rounded bg-slate-100" /></td>
                    <td className="px-3 py-3"><div className="h-4 w-20 animate-pulse rounded bg-slate-100" /></td>
                    <td className="px-4 py-3"><div className="h-9 w-full animate-pulse rounded bg-slate-100" /></td>
                  </tr>
                ))
              ) : productos.length === 0 ? (
                <tr>
                  <td colSpan={10} className="px-4 py-16 text-center text-slate-400">
                    {preparando ? (
                      <span className="inline-flex items-center gap-2">
                        <Loader2 size={16} className="animate-spin" />
                        Preparando el catálogo de WooCommerce…
                      </span>
                    ) : busqueda ? (
                      "Sin resultados para tu búsqueda."
                    ) : (
                      "No hay productos pendientes por crear."
                    )}
                  </td>
                </tr>
              ) : (
                productos.map((p) => {
                  const sel = seleccion.has(p.sku);
                  const url = urls[p.sku] ?? "";
                  const urlMala = sel && url.trim().length > 0 && !esAlibaba(url);
                  const esPadre =
                    (p.tipo === "padre" || p.tipo === "variable") &&
                    p.variantes.length > 0;
                  const abierto = esPadre && expandidos.has(p.sku);
                  // Estado de creación de ESTE producto (si está en la cola)
                  const prog = progreso.find((x) => x.sku === p.sku);
                  return (
                    <Fragment key={p.sku}>
                    <tr
                      className={[
                        "border-b border-slate-100 transition-colors",
                        sel ? "bg-indigo-50/50" : "hover:bg-slate-50",
                      ].join(" ")}
                    >
                      {/* Checkbox */}
                      <td className="px-4 py-4">
                        <input
                          type="checkbox"
                          checked={sel}
                          onChange={() => toggle(p.sku)}
                          className="h-4 w-4 cursor-pointer accent-indigo-600"
                        />
                      </td>
                      {/* Producto */}
                      <td className="px-4 py-4">
                        <div className="flex items-center gap-3">
                          <div className="group relative flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-slate-100 bg-white">
                            {p.imagen ? (
                              <>
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img src={p.imagen} alt="" loading="lazy" className="h-full w-full object-contain" />
                                {/* botón chico en la esquina: no tapa la imagen */}
                                <button
                                  onClick={() => setImagenModal({ src: p.imagen!, nombre: p.nombre })}
                                  title="Ver imagen grande"
                                  className="absolute bottom-0.5 right-0.5 rounded-md bg-white/90 p-1 text-slate-500 opacity-0 shadow ring-1 ring-slate-200 transition-opacity hover:text-indigo-600 group-hover:opacity-100"
                                >
                                  <Eye size={13} />
                                </button>
                              </>
                            ) : (
                              <ImageIcon size={26} className="text-slate-300" />
                            )}
                          </div>
                          <div className="min-w-0 max-w-[230px]">
                            <div className="line-clamp-2 text-sm font-semibold leading-snug text-slate-800">{p.nombre}</div>
                            <span className="font-mono text-xs text-slate-400">{p.sku}</span>
                            {prog && (
                              <div
                                className={[
                                  "mt-1 flex items-center gap-1.5 text-xs font-semibold",
                                  prog.estado === "error"
                                    ? "text-red-600"
                                    : prog.estado === "completado"
                                      ? "text-emerald-600"
                                      : "text-indigo-600",
                                ].join(" ")}
                                title={prog.paso}
                              >
                                {prog.estado === "error" ? (
                                  <AlertTriangle size={13} className="shrink-0" />
                                ) : prog.estado === "completado" ? (
                                  <CheckCircle2 size={13} className="shrink-0" />
                                ) : (
                                  <Loader2 size={13} className="shrink-0 animate-spin" />
                                )}
                                <span className="truncate">
                                  {prog.estado === "error"
                                    ? `Falló: ${prog.paso}`
                                    : prog.estado === "completado"
                                      ? "Creado ✓"
                                      : prog.paso}
                                </span>
                              </div>
                            )}
                          </div>
                        </div>
                      </td>
                      {/* Tipo: Padre o Único */}
                      <td className="px-3 py-4 text-center">
                        {esPadre ? (
                          <span className="inline-flex items-center gap-1 rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-xs font-bold text-violet-700">
                            <Layers size={13} /> Padre
                          </span>
                        ) : (
                          <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-semibold text-slate-500">
                            Único
                          </span>
                        )}
                      </td>
                      {/* Variantes: número + ver variantes */}
                      <td className="px-3 py-4 text-center">
                        {esPadre ? (
                          <button
                            onClick={() => toggleExpandido(p.sku)}
                            title="Ver todas las variantes del padre"
                            className="inline-flex items-center gap-1 rounded-lg border border-violet-200 bg-white px-2.5 py-1.5 text-xs font-bold text-violet-700 transition-colors hover:bg-violet-50"
                          >
                            <span className="tabular-nums">{p.variantes.length}</span>
                            <span className="font-semibold">
                              {abierto ? "Ocultar" : "Ver variantes"}
                            </span>
                            {abierto ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                          </button>
                        ) : (
                          <span className="text-xs text-slate-300">—</span>
                        )}
                      </td>
                      {/* Categoría de WooCommerce (luego será la de Mercado Libre) */}
                      <td className="px-3 py-4 text-xs text-slate-600">
                        {(() => {
                          const ruta = p.categoria_path.filter(
                            (c) => !/^(uncategorized|sin categor)/i.test(c.nombre),
                          );
                          if (!ruta.length) return <span className="text-slate-300">—</span>;
                          return (
                            <span title={ruta.map((c) => c.nombre).join(" › ")}>
                              {ruta[ruta.length - 1].nombre}
                            </span>
                          );
                        })()}
                      </td>
                      {/* Costo (costos_finales) */}
                      <td className="px-3 py-4 text-right font-semibold text-slate-700">
                        {p.costo != null ? precioMXN(p.costo) : "—"}
                      </td>
                      {/* Stock (padre = suma de variantes) */}
                      <td className="px-3 py-4 text-center font-semibold text-slate-600">
                        {p.stock ?? "—"}
                      </td>
                      {/* Valor = stock × costo */}
                      <td className="px-3 py-4 text-right font-bold text-slate-900">
                        {p.valor != null && p.valor > 0 ? precioMXN(p.valor) : "—"}
                      </td>
                      {/* Nº de contenedor (costos_validados) */}
                      <td className="px-3 py-4 text-xs">
                        {p.contenedor ? (
                          <span
                            title={p.contenedor}
                            className="inline-flex items-center gap-1 rounded-md border border-sky-200 bg-sky-50 px-2 py-1 font-mono font-semibold text-sky-700"
                          >
                            <Container size={12} className="shrink-0" />
                            {p.contenedor}
                          </span>
                        ) : (
                          <span className="text-slate-300">—</span>
                        )}
                      </td>
                      {/* URL de Alibaba */}
                      <td className="px-4 py-4">
                        <div className="relative">
                          <Link2
                            size={15}
                            className={[
                              "pointer-events-none absolute left-3 top-1/2 -translate-y-1/2",
                              urlMala ? "text-red-400" : "text-slate-400",
                            ].join(" ")}
                          />
                          <input
                            value={url}
                            onChange={(e) => setUrl(p.sku, e.target.value)}
                            onFocus={() => { if (!sel) toggle(p.sku); }}
                            placeholder="https://…alibaba.com/…"
                            className={[
                              "w-full rounded-lg border bg-white py-2 pl-9 pr-3 text-sm outline-none transition-shadow placeholder:text-slate-300 focus:ring-2",
                              urlMala ? "border-red-300 text-red-700" : "border-slate-200 text-slate-700",
                            ].join(" ")}
                            style={{ outlineColor: ACENTO }}
                          />
                        </div>
                        {urlMala && (
                          <div className="mt-1 text-[11px] text-red-500">
                            Debe ser una URL de Alibaba (o 1688.com).
                          </div>
                        )}
                      </td>
                    </tr>
                    {/* Variantes del padre (desplegable) */}
                    {abierto && (
                      <tr className="border-b border-slate-100 bg-violet-50/40">
                        <td />
                        <td colSpan={9} className="px-4 pb-4 pt-1">
                          <div className="rounded-xl border border-violet-100 bg-white p-3">
                            <div className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-violet-600">
                              <Layers size={13} /> {p.variantes.length} variante(s)
                            </div>
                            <table className="w-full text-xs">
                              <thead>
                                <tr className="text-left text-[10px] uppercase tracking-wide text-slate-400">
                                  <th className="py-1 pr-3 font-semibold">SKU</th>
                                  <th className="py-1 pr-3 font-semibold">Variante</th>
                                  <th className="py-1 pr-3 text-right font-semibold">Costo</th>
                                  <th className="py-1 pr-3 text-center font-semibold">Stock</th>
                                  <th className="py-1 pr-3 text-right font-semibold">Valor</th>
                                  <th className="py-1 font-semibold">Contenedor</th>
                                </tr>
                              </thead>
                              <tbody>
                                {p.variantes.map((v) => (
                                  <tr key={v.sku} className="border-t border-slate-100">
                                    <td className="py-1.5 pr-3 font-mono text-slate-500">{v.sku}</td>
                                    <td className="py-1.5 pr-3 font-medium text-slate-700">
                                      {v.nombre ?? "—"}
                                    </td>
                                    <td className="py-1.5 pr-3 text-right font-semibold text-slate-800">
                                      {v.costo != null ? precioMXN(v.costo) : "—"}
                                    </td>
                                    <td className="py-1.5 pr-3 text-center text-slate-600">
                                      {v.stock ?? "—"}
                                    </td>
                                    <td className="py-1.5 pr-3 text-right font-bold text-slate-900">
                                      {v.valor != null && v.valor > 0 ? precioMXN(v.valor) : "—"}
                                    </td>
                                    <td className="py-1.5 font-mono text-sky-700">
                                      {v.contenedor ?? "—"}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </td>
                      </tr>
                    )}
                    </Fragment>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Paginación inferior */}
        <div className="mt-6 rounded-xl border border-slate-200 bg-white px-4 py-3">
          <Pagination
            pag={pag}
            color={COLOR}
            textoColor="#FFFFFF"
            onPage={irPagina}
            sincronizando={!indiceCompleto || cargando}
          />
        </div>
      </main>

      {/* Modal de imagen grande */}
      {imagenModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
          onClick={() => setImagenModal(null)}
        >
          <div
            className="relative max-h-[92vh] max-w-6xl overflow-hidden rounded-2xl bg-white p-4 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setImagenModal(null)}
              title="Cerrar"
              className="absolute right-3 top-3 z-10 rounded-full bg-white/90 p-1.5 text-slate-500 shadow hover:text-slate-800"
            >
              <X size={18} />
            </button>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {/* Se muestra a 3× su tamaño REAL (limitado a la pantalla): grande
                pero sin licuar miniaturas de baja resolución. */}
            <img
              src={imagenModal.src}
              alt={imagenModal.nombre}
              onLoad={(e) => {
                const el = e.currentTarget;
                const ancho = Math.min(
                  el.naturalWidth * 3,
                  window.innerWidth * 0.82,
                  1400,
                );
                el.style.width = `${ancho}px`;
                el.style.height = "auto";
              }}
              className="max-h-[78vh] rounded-lg object-contain"
            />
            <div className="mt-3 text-center text-sm font-semibold text-slate-700">
              {imagenModal.nombre}
            </div>
          </div>
        </div>
      )}

      {/* Barra de acción fija */}
      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-3 text-sm">
            {resultado ? (
              <span
                className={[
                  "flex items-center gap-2 font-medium",
                  resultado.tipo === "ok" ? "text-emerald-600" : "text-red-600",
                ].join(" ")}
              >
                {resultado.tipo === "ok" ? (
                  <CheckCircle2 size={16} />
                ) : (
                  <AlertTriangle size={16} />
                )}
                {resultado.texto}
              </span>
            ) : (
              <span className="text-slate-500">
                <span className="font-bold text-indigo-600">{totalSel}</span> producto(s)
                seleccionado(s)
                {totalSel > 0 && selSinUrl.length > 0 && (
                  <span className="ml-2 text-amber-600">
                    · faltan {selSinUrl.length} URL(s) de Alibaba
                  </span>
                )}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            {totalSel > 0 && (
              <button
                onClick={() => { setSeleccion(new Set()); setResultado(null); }}
                className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-500 transition-colors hover:bg-slate-50"
              >
                Limpiar
              </button>
            )}
            <button
              onClick={enviar}
              disabled={!puedeCrear}
              className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-bold text-white shadow-sm transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
              style={{ backgroundColor: COLOR }}
            >
              {enviando ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <PackagePlus size={16} />
              )}
              Crear productos
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
