"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Search, Filter, RotateCw, Sparkles, Layers, Loader2,
  ShoppingCart, AlertTriangle, Info,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import MarketplaceTabs from "@/components/MarketplaceTabs";
import AccountTabs from "@/components/AccountTabs";
import ChannelLegend from "@/components/ChannelLegend";
import Pagination from "@/components/Pagination";
import ProductGrid from "@/components/ProductGrid";
import ProductList from "@/components/ProductList";
import ProductControls, { type Vista } from "@/components/ProductControls";
import ProductDetailDrawer from "@/components/ProductDetailDrawer";
import ResumenPublicacionesCanal from "@/components/ResumenPublicacionesCanal";

import { listarCanales, listarProductos, listarCategorias, type CategoriaWC } from "@/lib/api";
import type { CanalInfo, FiltroActivas, Paginacion, Producto } from "@/lib/types";
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
  // "Solo activas": lo que se puede COMPRAR hoy, con el criterio de cada canal.
  // Excluyente con `soloPublicados` porque en el backend `solo_activas` MANDA
  // sobre él (no se suman con AND): tenerlos encendidos a la vez mostraría un
  // chip que no está haciendo nada.
  const [soloActivas, setSoloActivas] = useState(false);
  // Qué hizo el backend con la petición del filtro. `null` = nadie lo pidió.
  // NO es decorativo: distingue "0 activas de verdad" de "no se pudo filtrar".
  const [filtroActivas, setFiltroActivas] = useState<FiltroActivas | null>(null);
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
  // "Costo validado": solo los productos con la marca `revisado_at`
  // (migración 0032). Vive solo en General — es donde el backend puede
  // cruzar la marca contra el catálogo de la tienda y paginar sobre el
  // subconjunto. Se acumula con la búsqueda y con "Filtrar SKUs".
  const [revisado, setRevisado] = useState(false);
  const [categorias, setCategorias] = useState<CategoriaWC[]>([]);

  const topRef = useRef<HTMLDivElement>(null);

  // ── Canal activo + tema ─────────────────────────────────────────────
  const canalActivo = useMemo(
    () => canales.find((c) => c.id === canal),
    [canales, canal],
  );
  const esGeneral = canal === GENERAL;

  // ── Las DOS lecturas de `filtro_activas` ────────────────────────────
  // Se separan a propósito: pintarlas igual es exactamente el error que el
  // bloque existe para evitar.
  //   · `aplicado: false` → la lista NO está filtrada aunque se haya pedido.
  //     Callarlo hace creer al usuario que está viendo activas cuando ve todo.
  //   · `aplicado: true` + total 0 → el CERO ES LA RESPUESTA (TikTok hoy).
  //     Nunca "no encontrados" ni "preparando": el canal sí contestó.
  const activasNoAplicado = soloActivas && !!filtroActivas && !filtroActivas.aplicado;
  const activasCeroReal =
    soloActivas && !!filtroActivas && filtroActivas.aplicado
    && pag.total === 0 && !cargando;
  // La `nota` del canal cuando el filtro SÍ se aplicó: es la que explica el
  // salto de Amazon (1,668 publicados → 138 activas) y el cero de TikTok.
  const activasNota =
    soloActivas && filtroActivas?.aplicado ? filtroActivas.nota : null;

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
        soloActivas,
        cuenta: esGeneral ? null : cuenta,
        orden,
        estados,
        categoria: esGeneral ? categoria : null,
        revisado: esGeneral ? revisado : false,
        // Omnicanal es la vista de CONTROL: muestra TODO el catálogo, incluidos
        // los drafts. Esconderlos hacía invisible un producto en draft pero VIVO
        // en un canal (TEC-1841-ROS vendió estando oculto; ver v0.29.0).
        vista: "omnicanal",
      },
      ctrl.signal,
    )
      .then((r) => {
        setProductos(r.items);
        setPag(r.paginacion);
        // Viene sólo si se pidió el filtro. Se guarda SIEMPRE (aunque sea
        // `null`) para no arrastrar la nota de una petición anterior.
        setFiltroActivas(r.filtro_activas ?? null);
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
    // `soloActivas` va aquí SÍ O SÍ: sin él, encender el chip no vuelve a
    // pedir y la rejilla se queda igual — se ve como que el filtro no sirve.
  }, [canal, page, busqueda, skusFiltro, soloPublicados, soloActivas, cuenta, esGeneral, orden, estados, categoria, revisado]);

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
    // "Solo activas" arranca apagado en cada pestaña. Además de higiene, es lo
    // que sostiene la invariante de la que depende el reintento de abajo: el
    // chip no existe en General y tampoco puede llegar encendido desde otro
    // canal, así que `esGeneral && soloActivas` es inalcanzable.
    setSoloActivas(false);
    setFiltroActivas(null);
    // Reiniciar filtros que dependen del canal
    setCategoria(null);
    setEstados([]);
    setRevisado(false);
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

            {/* Toggles de marketplace. "Publicado" y "ACTIVO" no son lo mismo y
                por eso son dos chips, no uno. Ninguno se ofrece en GENERAL: Woo
                es la FUENTE del catálogo y del stock, no un canal de venta, así
                que no tiene estado de publicación que filtrar (el backend
                contestaría `aplicado: false`). */}
            {!esGeneral && (
              <>
                <button
                  onClick={() => {
                    const nuevo = !soloPublicados;
                    setSoloPublicados(nuevo);
                    // Excluyentes: en el backend `solo_activas` MANDA sobre
                    // `solo_publicados` (no se suman con AND), así que dejar
                    // los dos encendidos pintaría un chip que no filtra nada.
                    if (nuevo) setSoloActivas(false);
                    setPage(1);
                  }}
                  className={[
                    "flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-semibold transition-colors",
                    soloPublicados
                      ? "border-transparent text-white"
                      : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
                  ].join(" ")}
                  style={soloPublicados ? { backgroundColor: tema.color, color: tema.texto } : undefined}
                  // "Publicado" NO es "activo", y este filtro cuenta lo primero:
                  // en Mercado Libre deja pasar las PAUSADAS y en Amazon las
                  // DISCOVERABLE, que se ven y no se venden. Para lo segundo
                  // está el chip de al lado.
                  title={
                    "Deja las que existen en el canal: `listing_id` presente y no cerrada.\n"
                    + "INCLUYE pausadas y, en Amazon, las que se ven pero no se pueden "
                    + "comprar (DISCOVERABLE).\n"
                    + "Para quedarte sólo con las que se pueden comprar hoy, usa "
                    + "«Solo activas»."
                  }
                >
                  <Filter size={15} />
                  Solo publicados
                </button>

                <button
                  onClick={() => {
                    const nuevo = !soloActivas;
                    setSoloActivas(nuevo);
                    if (nuevo) setSoloPublicados(false);
                    else setFiltroActivas(null);
                    setPage(1);
                  }}
                  className={[
                    "flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-semibold transition-colors",
                    soloActivas
                      ? "border-transparent text-white"
                      : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50",
                  ].join(" ")}
                  style={soloActivas ? { backgroundColor: tema.color, color: tema.texto } : undefined}
                  title={
                    "Deja sólo lo que se puede COMPRAR hoy, con el criterio de cada canal.\n"
                    + "En Amazon el corte es grande y es CORRECTO: sus 1,253 DISCOVERABLE "
                    + "se ven en el catálogo y no se pueden comprar (1,668 publicados → "
                    + "138 activas).\n"
                    + "En TikTok da 0, y también es correcto: sus 283 APPROVED están "
                    + "SELLER_DEACTIVATED.\n"
                    + "Manda sobre «Solo publicados»: encender éste apaga aquél."
                  }
                >
                  <ShoppingCart size={15} />
                  Solo activas
                </button>
              </>
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
            revisado={revisado}
            onRevisado={(v) => { setRevisado(v); setPage(1); }}
            color={tema.color}
            textoColor={tema.texto}
          />
        </div>

        {/* Censo del canal: cuántas publicaciones hay y cuántas están ACTIVAS
            con el criterio de ESTE canal. Va aquí, arriba de la lista, y no en
            otra pestaña: es el encabezado de lo que se está mirando. En General
            no aplica —Woo es la fuente del catálogo, no un canal de venta. */}
        {!esGeneral && (
          <div className="mt-4">
            <ResumenPublicacionesCanal canal={canal} cuenta={cuenta} color={tema.color} />
          </div>
        )}

        {/* LECTURA 1 — se pidió el filtro y el canal NO pudo aplicarlo. Lo que
            se ve abajo es el catálogo SIN filtrar: hay que decirlo, o el chip
            encendido miente. */}
        {activasNoAplicado && (
          <div className="mt-4 flex items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <AlertTriangle size={18} className="mt-0.5 shrink-0 text-amber-500" />
            <span>
              <strong>La lista de abajo NO está filtrada por activas.</strong>{" "}
              Se pidió el filtro y este canal no puede evaluarlo, así que estás
              viendo el catálogo completo.
              {filtroActivas?.nota ? ` ${filtroActivas.nota}` : null}
            </span>
          </div>
        )}

        {/* LECTURA 2 — el filtro SÍ se aplicó: la nota explica la trampa del
            canal (el salto de Amazon, el cero de TikTok, el "puede estar
            activa" de Temu). */}
        {activasNota && (
          <div className="mt-4 flex items-start gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-2.5 text-xs text-slate-600">
            <Info size={14} className="mt-0.5 shrink-0 text-slate-400" />
            <span>{activasNota}</span>
          </div>
        )}

        {/* Paginación superior */}
        <div className="mt-4 rounded-xl border border-slate-200 bg-white px-4 py-3">
          <Pagination pag={pag} color={tema.color} textoColor={tema.texto} onPage={irPagina} />
        </div>

        {/* Productos: mosaico o lista */}
        <div className="mt-5">
          {activasCeroReal ? (
            // El canal contestó y la respuesta es CERO. Va aquí, en lugar de la
            // rejilla, porque ProductGrid/ProductList dirían "No se encontraron
            // productos · Prueba con otra búsqueda" — que se lee como "no hay
            // nada" o "todavía no carga", y las dos lecturas son falsas.
            <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-slate-300 bg-white py-24 text-center">
              <ShoppingCart size={48} className="text-slate-300" strokeWidth={1.3} />
              <p className="text-base font-semibold text-slate-600">
                0 activas en este canal
              </p>
              <p className="max-w-xl px-6 text-sm text-slate-500">
                No es un error ni una carga a medias: el canal sí contestó la
                pregunta y la respuesta es cero. Hoy no hay ninguna publicación
                que se pueda comprar aquí.
              </p>
              {filtroActivas?.nota && (
                <p className="max-w-xl px-6 text-xs text-slate-400">
                  {filtroActivas.nota}
                </p>
              )}
            </div>
          ) : vista === "mosaico" ? (
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
