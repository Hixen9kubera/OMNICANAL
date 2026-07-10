"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Search, Filter, RotateCw, Sparkles, Layers, Loader2 } from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import MarketplaceTabs from "@/components/MarketplaceTabs";
import AccountTabs from "@/components/AccountTabs";
import ChannelLegend from "@/components/ChannelLegend";
import Pagination from "@/components/Pagination";
import ProductGrid from "@/components/ProductGrid";
import ProductList from "@/components/ProductList";
import ProductControls, { type Vista } from "@/components/ProductControls";
import ProductDetailDrawer from "@/components/ProductDetailDrawer";

import { listarCanales, listarProductos, listarCategorias, type CategoriaWC } from "@/lib/api";
import type { CanalInfo, Paginacion, Producto } from "@/lib/types";
import { THEME_FALLBACK, hexToRgba, variablesTema, type CanalTheme } from "@/lib/theme";

const PER_PAGE = 40;
const GENERAL = "general";

export default function OmnicanalPage() {
  const [canales, setCanales] = useState<CanalInfo[]>([]);
  const [canal, setCanal] = useState<string>(GENERAL);
  const [cuenta, setCuenta] = useState<string | null>(null);

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
  const [soloPublicados, setSoloPublicados] = useState(false);
  const [cargando, setCargando] = useState(true);
  // Arranque en frío del backend: el índice puede tardar varios segundos en
  // construirse. "0 resultados" en ese momento no significa catálogo vacío.
  const [preparando, setPreparando] = useState(false);
  const reintentos = useRef(0);
  // true hasta que la carga de este CANAL termine al menos una vez (con o sin
  // filtro). Evita ver "No se encontraron productos" al entrar a una pestaña,
  // antes de que llegue la respuesta real.
  const primeraCarga = useRef(true);
  const [sel, setSel] = useState<Producto | null>(null);

  // Vista, orden y filtros
  const [vista, setVista] = useState<Vista>("mosaico");
  const [orden, setOrden] = useState("reciente");
  const [estados, setEstados] = useState<string[]>([]);
  const [categoria, setCategoria] = useState<number | null>(null);
  const [categorias, setCategorias] = useState<CategoriaWC[]>([]);

  const topRef = useRef<HTMLDivElement>(null);

  // ── Canal activo + tema ─────────────────────────────────────────────
  const canalActivo = useMemo(
    () => canales.find((c) => c.id === canal),
    [canales, canal],
  );
  const esGeneral = canal === GENERAL;

  const tema: CanalTheme = useMemo(() => {
    const fb = THEME_FALLBACK[canal] ?? THEME_FALLBACK.general;
    if (!canalActivo) return fb;
    return {
      color: canalActivo.color,
      texto: canalActivo.color_texto,
      acento: canalActivo.acento,
      suave: fb.suave ?? hexToRgba(canalActivo.color, 0.1),
    };
  }, [canalActivo, canal]);

  const colorMap = useMemo(
    () => Object.fromEntries(canales.map((c) => [c.id, c.color])),
    [canales],
  );
  const labelMap = useMemo(
    () => Object.fromEntries(canales.map((c) => [c.id, c.label])),
    [canales],
  );

  // ── Carga inicial de canales ────────────────────────────────────────
  useEffect(() => {
    listarCanales()
      .then(setCanales)
      .catch(() => setCanales([]));
    listarCategorias()
      .then(setCategorias)
      .catch(() => setCategorias([]));
  }, []);

  // ── Debounce de búsqueda ────────────────────────────────────────────
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

  // ── Carga de productos ──────────────────────────────────────────────
  const cargar = useCallback(() => {
    const ctrl = new AbortController();
    setCargando(true);
    listarProductos(
      {
        canal,
        page,
        perPage: PER_PAGE,
        search: busqueda || undefined,
        skus: skusFiltro || undefined,
        soloPublicados,
        cuenta: esGeneral ? null : cuenta,
        orden,
        estados,
        categoria: esGeneral ? categoria : null,
      },
      ctrl.signal,
    )
      .then((r) => {
        setProductos(r.items);
        setPag(r.paginacion);
        // Sin búsqueda/filtro y 0 resultados → probablemente el índice todavía
        // se está construyendo (arranque en frío). Reintenta en vez de mostrar
        // "no encontrados". Solo aplica al canal GENERAL (WooCommerce);
        // ML/Amazon leen de MySQL propio y no tienen este arranque en frío.
        if (esGeneral && !busqueda && !skusFiltro && r.paginacion.total === 0 && reintentos.current < 45) {
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
  }, [canal, page, busqueda, skusFiltro, soloPublicados, cuenta, esGeneral, orden, estados, categoria]);

  useEffect(() => cargar(), [cargar]);

  // ── Cambio de canal ─────────────────────────────────────────────────
  function seleccionarCanal(nuevo: string) {
    if (nuevo === canal) return;
    setCanal(nuevo);
    setPage(1);
    const info = canales.find((c) => c.id === nuevo);
    // Cuenta por defecto (Mercado Libre → Kubera/BEKURA)
    const def = info?.subcuentas.find((s) => s.es_default)?.id ?? null;
    setCuenta(def);
    // Marketplaces: por defecto mostrar solo publicados
    setSoloPublicados(nuevo !== GENERAL);
    // Reiniciar filtros que dependen del canal
    setCategoria(null);
    setEstados([]);
    setOrden("reciente");
    // Buscador y "Filtrar SKUs": cada pestaña empieza limpia (evita que un
    // filtro de un canal se reaplique sin querer al cambiar a otro).
    setBusquedaInput("");
    setBusqueda("");
    setSkusInput("");
    setSkusFiltro("");
    // Nueva pestaña: trátala como "primera carga" hasta que responda.
    primeraCarga.current = true;
    reintentos.current = 0;
  }

  function irPagina(p: number) {
    setPage(p);
    topRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <div className="min-h-screen" style={variablesTema(tema)}>
      <AppNavbar />

      <main className="mx-auto max-w-[1600px] px-4 pb-16 pt-6 sm:px-6">
        {/* Banner del canal activo (cambia de color) */}
        <div
          ref={topRef}
          className="relative overflow-hidden rounded-3xl p-6 shadow-card transition-colors duration-300"
          style={{
            background: `linear-gradient(120deg, ${tema.color} 0%, ${hexToRgba(
              tema.acento,
              0.92,
            )} 100%)`,
            color: tema.texto,
          }}
        >
          <div className="relative z-10 flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.2em] opacity-80">
                Centro Omnicanal · WooCommerce
              </div>
              <h1 className="mt-1 text-3xl font-extrabold tracking-tight">
                {canalActivo?.label ?? "General"}
              </h1>
              <p className="mt-1 max-w-xl text-sm opacity-90">
                {canalActivo?.descripcion ??
                  "Todas las publicaciones de tu catálogo."}
              </p>
            </div>
            <div className="text-right">
              <div className="text-4xl font-black tabular-nums">
                {new Intl.NumberFormat("es-MX").format(pag.total)}
              </div>
              <div className="text-xs font-semibold uppercase tracking-wide opacity-80">
                {esGeneral ? "productos" : "publicaciones"}
              </div>
            </div>
          </div>
          {/* Decoración */}
          <div
            className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full opacity-20"
            style={{ background: tema.texto }}
          />
        </div>

        {/* Pestañas de marketplace */}
        <div className="mt-6">
          <MarketplaceTabs
            canales={canales}
            activo={canal}
            onSelect={seleccionarCanal}
          />
        </div>

        {/* Sub-cuentas (Mercado Libre) + buscador + filtros */}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            {canalActivo?.subcuentas?.length ? (
              <AccountTabs
                subcuentas={canalActivo.subcuentas}
                activa={cuenta}
                color={tema.color}
                textoColor={tema.texto}
                onSelect={(c) => {
                  setCuenta(c);
                  setPage(1);
                }}
              />
            ) : null}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {/* Leyenda de canales (solo en GENERAL, donde están los puntos) */}
            {esGeneral && <ChannelLegend canales={canales} />}

            {/* Toggle solo publicados (marketplaces) */}
            {!esGeneral && (
              <button
                onClick={() => {
                  setSoloPublicados((v) => !v);
                  setPage(1);
                }}
                className={[
                  "flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-semibold transition-colors",
                  soloPublicados
                    ? "border-transparent text-white"
                    : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
                ].join(" ")}
                style={soloPublicados ? { backgroundColor: tema.color, color: tema.texto } : undefined}
              >
                <Filter size={15} />
                Solo publicados
              </button>
            )}

            {/* Buscador */}
            <div className="relative">
              <Search
                size={16}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <input
                value={busquedaInput}
                onChange={(e) => setBusquedaInput(e.target.value)}
                placeholder="SKU o nombre…"
                className="w-64 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-700 outline-none transition-shadow placeholder:text-slate-400 focus:ring-2"
                style={{ outlineColor: tema.acento }}
              />
            </div>

            {/* Filtrar SKUs: multi-término separado por coma */}
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
                style={{ outlineColor: tema.acento }}
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
        </div>

        {/* Controles: vista mosaico/lista, orden, categoría, filtro de estado */}
        <div className="mt-5">
          <ProductControls
            vista={vista}
            onVista={setVista}
            orden={orden}
            onOrden={(o) => { setOrden(o); setPage(1); }}
            esGeneral={esGeneral}
            categorias={categorias}
            categoria={categoria}
            onCategoria={(c) => { setCategoria(c); setPage(1); }}
            estados={estados}
            onEstados={(e) => { setEstados(e); setPage(1); }}
            color={tema.color}
            textoColor={tema.texto}
          />
        </div>

        {/* Paginación superior */}
        <div className="mt-4 rounded-xl border border-slate-200 bg-white px-4 py-3">
          <Pagination pag={pag} color={tema.color} textoColor={tema.texto} onPage={irPagina} />
        </div>

        {/* Productos: mosaico o lista */}
        <div className="mt-5">
          {vista === "mosaico" ? (
            <ProductGrid
              productos={productos}
              canal={canal}
              esGeneral={esGeneral}
              cargando={cargando || (productos.length === 0 && primeraCarga.current)}
              preparando={preparando}
              color={tema.color}
              colorMap={colorMap}
              labelMap={labelMap}
              onSelect={(p) => setSel(p)}
            />
          ) : (
            <ProductList
              productos={productos}
              esGeneral={esGeneral}
              cargando={cargando || (productos.length === 0 && primeraCarga.current)}
              preparando={preparando}
              color={tema.color}
              colorMap={colorMap}
              labelMap={labelMap}
              onSelect={(p) => setSel(p)}
            />
          )}
        </div>

        {/* Paginación inferior */}
        <div className="mt-6 rounded-xl border border-slate-200 bg-white px-4 py-3">
          <Pagination pag={pag} color={tema.color} textoColor={tema.texto} onPage={irPagina} />
        </div>

        {/* Aviso de canal de ejemplo */}
        {canalActivo && !canalActivo.habilitado && (
          <div className="mt-6 flex items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <Sparkles size={18} className="shrink-0 text-amber-500" />
            <span>
              <strong>{canalActivo.label}</strong> muestra datos de ejemplo. Cuando
              integres sus credenciales, este canal traerá información real
              automáticamente.
            </span>
          </div>
        )}
      </main>

      {/* Drawer de detalle 360° */}
      <ProductDetailDrawer
        sku={sel?.sku ?? null}
        producto={sel}
        canales={canales}
        onClose={() => setSel(null)}
      />
    </div>
  );
}
