"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  Box,
  Sparkles,
  PackageSearch,
  ShoppingBag,
  BookOpen,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import Pagination from "@/components/Pagination";
import ResolverCostosModal from "@/components/ResolverCostosModal";
import ValidarPublicadosModal from "@/components/ValidarPublicadosModal";
import ComoValidarCostosModal from "@/components/ComoValidarCostosModal";
import CajaMasterPanel from "@/components/CajaMasterPanel";
import ChipRevision from "@/components/ChipRevision";
import { ChipMoneda, EntradaMoneda, TituloMoneda } from "@/components/Moneda";
import {
  ACENTO,
  COLOR,
  TARIFA_CBM,
  TIPO_CAMBIO_DEFAULT,
} from "@/components/resolver/comunes";
import { listarCostos, contenedoresCosto, costoBulk, costoPreview } from "@/lib/api";
import type { CostoRow, ContenedorInfo, Paginacion, CostoBulkResp, CostoCalculo } from "@/lib/types";

const PER_PAGE = 50;
/** Viñeta bajo el rótulo de una columna: la operación que produce ese número. */
const NOTA_TH =
  "mt-0.5 text-[9px] font-normal normal-case tracking-normal text-slate-400";
// Tipo de cambio USD→MXN por defecto (editable en la barra de abajo).
// Vive en components/resolver/comunes.ts junto con TARIFA_CBM y el color: es el
// MISMO que usan los dos modales de resolución. Tenerlo duplicado hacía que el
// mismo costo en dólares diera dos costos en pesos según por dónde entrara la
// captura.
const DEFAULT_TC = TIPO_CAMBIO_DEFAULT;
const mxnToUsd = (v: number | null | undefined, tc: number) =>
  v == null ? "" : String(Math.round((v / (tc || DEFAULT_TC)) * 100) / 100);

function precioMXN(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(v);
}
const dims = (r: CostoRow) =>
  r.largo && r.ancho && r.alto ? `${r.largo}×${r.ancho}×${r.alto}` : "—";

// Edición inline por SKU (valores como string para inputs controlados).
/**
 * Una fila se puede capturar de dos maneras, y la diferencia importa mucho:
 *
 *   "individual" — lo tecleado ES la pieza. Es como funcionaba siempre.
 *   "caja"       — lo tecleado es el CARTÓN MASTER, y la pieza se deriva
 *                  repartiendo el volumen entre `piezas_por_caja`.
 *
 * Sin este interruptor la gente escribía las medidas del cartón en los campos
 * de pieza y el flete salía por caja (84×33×60 daba $1,247 en vez de $124.74),
 * o convertía a mano dividiendo CADA LADO — que da volumen ÷ n³ y dejó 270 SKUs
 * del catálogo con densidades imposibles.
 */
type ModoCaptura = "individual" | "caja";
type Edicion = {
  modo: ModoCaptura; piezas_por_caja: string;
  largo: string; ancho: string; alto: string; peso: string; costo_producto: string;
};

/** Reparte el volumen del cartón entre sus piezas, conservándolo exacto. */
function piezaDesdeCaja(
  L: number, W: number, H: number, piezas: number,
): [number, number, number] {
  if (!L || !W || !H || piezas <= 0) return [L, W, H];
  if (piezas <= 1) return [L, W, H];
  const lados: [number, number, number] = [L, W, H];
  if (piezas <= 10) {
    // Pocas piezas: van formadas en fila a lo largo del lado mayor.
    const i = lados.indexOf(Math.max(...lados)) as 0 | 1 | 2;
    lados[i] = lados[i] / piezas;
  } else {
    // Muchas: nadie apila 120 piezas en hilera. La raíz cúbica reparte el
    // encogimiento entre los tres lados y da una forma plausible.
    const f = Math.cbrt(1 / piezas);
    lados[0] *= f; lados[1] *= f; lados[2] *= f;
  }
  return [
    Math.round(lados[0] * 100) / 100,
    Math.round(lados[1] * 100) / 100,
    Math.round(lados[2] * 100) / 100,
  ];
}

/** Valores POR PIEZA de una edición, sea cual sea el modo en que se capturó. */
function porPieza(ed: Edicion) {
  const L = Number(ed.largo) || 0, W = Number(ed.ancho) || 0, H = Number(ed.alto) || 0;
  const peso = Number(ed.peso) || 0;
  if (ed.modo === "individual") return { largo: L, ancho: W, alto: H, peso };
  const pzs = Number(ed.piezas_por_caja) || 0;
  if (pzs <= 0) return { largo: L, ancho: W, alto: H, peso };
  const [l, a, h] = piezaDesdeCaja(L, W, H, pzs);
  return { largo: l, ancho: a, alto: h, peso: Math.round((peso / pzs) * 1000) / 1000 };
}
const s = (v: number | null | undefined) => (v == null ? "" : String(v));
const n = (v: string): number | null => (v.trim() ? Number(v) || null : null);
// costo_producto se edita en USD (guardado en MXN → se muestra ÷ TC).
const seedEdicion = (r: CostoRow, tc: number): Edicion => ({
  // Se abre en "individual" porque es lo que la fila ya trae guardado; cambiar
  // a "caja" es un acto deliberado del usuario.
  modo: "individual", piezas_por_caja: "",
  largo: s(r.largo), ancho: s(r.ancho), alto: s(r.alto), peso: s(r.peso),
  costo_producto: mxnToUsd(r.costo_producto, tc),
});

/**
 * Costo de producto en PESOS de una fila, respetando lo que el usuario tecleó.
 *
 * El campo se captura en DÓLARES pero lo que se guarda son pesos, y la ida y
 * vuelta no es exacta: $1,387.50 ÷ 19 = $73.03 redondeado, y ×19 regresa como
 * $1,387.57. Siete centavos que aparecían con solo SELECCIONAR la fila, y que
 * "Regenerar y guardar" escribía en la base aunque nadie hubiera tocado nada.
 * Con la columna en pesos al lado de la de dólares el desfase quedó a la vista.
 *
 * La regla: si el campo sigue siendo idéntico al que se sembró, no hubo
 * captura y manda el valor GUARDADO. Solo se convierte lo que de verdad se
 * tecleó.
 */
/**
 * Producto + flete, sumados EN CENTAVOS ENTEROS.
 *
 * Cada sumando se redondea a centavos ANTES de sumar —que es como se pintan las
 * dos columnas— y la suma se hace en enteros para que no aparezca la basura de
 * los flotantes (`102.98 + 63.16` da `166.14000000000001` sumando en pesos).
 * Así la columna de la derecha es EXACTAMENTE lo que el ojo suma a su izquierda.
 *
 * `respaldo` solo se usa cuando falta uno de los dos sumandos.
 */
function sumaEnCentavos(
  a: number | null, b: number | null, respaldo: number | null,
): number | null {
  if (a == null || b == null) return respaldo;
  return (Math.round(a * 100) + Math.round(b * 100)) / 100;
}

function prodMxnDe(r: CostoRow, ed: Edicion | undefined, tc: number): number | null {
  if (!ed) return r.costo_producto;
  const usd = (ed.costo_producto ?? "").trim();
  if (usd === mxnToUsd(r.costo_producto, tc)) return r.costo_producto;
  const v = n(usd);
  return v != null ? Math.round(v * tc * 100) / 100 : r.costo_producto;
}

export default function CostosPage() {
  // "Resolver": compara un packing list contra estos costos. Vive aquí porque
  // su resultado se escribe justo en esta tabla.
  const [resolverAbierto, setResolverAbierto] = useState(false);
  // "Validar costo de PUBLICADOS EN ML": el mismo trabajo, pero al revés — parte
  // de los SKUs seleccionados y a cada uno le busca su renglón en el packing
  // list. Solo aplica a productos con publicación viva en Mercado Libre.
  const [publicadosAbierto, setPublicadosAbierto] = useState(false);
  // El tutorial. Es contenido estático: no toca la selección ni pide nada al
  // backend, así que puede abrirse con el panel a medio trabajo.
  const [comoAbierto, setComoAbierto] = useState(false);
  // Explicación en vez de un botón muerto cuando no hay nada seleccionado.
  const [avisoPublicados, setAvisoPublicados] = useState<string | null>(null);
  // Chip "Solo publicados en ML": el filtro lo resuelve el backend (`exists`
  // contra channel.listings) porque la lista de publicaciones no cabe aquí.
  const [soloPublicadosMl, setSoloPublicadosMl] = useState(false);
  // SKU cuya CAJA MASTER se está capturando (null = panel cerrado).
  const [cajaMaster, setCajaMaster] = useState<string | null>(null);
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

  // Desglose por SKU seleccionado. Se pide al backend porque la COMISIÓN sale
  // de la categoría real de ML: calcularla aquí sería adivinar.
  const [desglose, setDesglose] = useState<Record<string, CostoCalculo | null>>({});
  // Con muchas filas abiertas serían decenas de llamadas por tecla. Arriba de
  // este número la tabla se usa para el bulk, no para estudiar un SKU.
  const MAX_DESGLOSE = 12;

  useEffect(() => {
    const skus = [...seleccion];
    if (!skus.length || skus.length > MAX_DESGLOSE) { setDesglose({}); return; }
    let vivo = true;
    const t = setTimeout(async () => {
      for (const sku of skus) {
        const ed = ediciones[sku];
        if (!ed) continue;
        const pz = porPieza(ed);
        const cpUsd = n(ed.costo_producto);
        try {
          const r = await costoPreview(sku, {
            costo_producto: cpUsd != null ? cpUsd * tcNum() : null,
            largo: pz.largo || null, ancho: pz.ancho || null, alto: pz.alto || null,
            peso: pz.peso || null,
            margen: (Number(margenBulk) || 0) / 100,
            pct_comision: comisionBulk.trim() ? Number(comisionBulk) / 100 : null,
            incluir_envio: envioBulk,
            auto_cbm: true,
          });
          if (!vivo) return;
          setDesglose((d) => ({ ...d, [sku]: r.calculo }));
        } catch {
          if (!vivo) return;
          setDesglose((d) => ({ ...d, [sku]: null }));
        }
      }
    }, 450);
    return () => { vivo = false; clearTimeout(t); };
  }, [seleccion, ediciones, margenBulk, comisionBulk, envioBulk, tcBulk]);

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
        soloPublicadosMl: soloPublicadosMl || undefined,
      },
      ctrl.signal,
    )
      .then((r) => { setRows(r.items); setPag(r.paginacion); primeraCarga.current = false; })
      .catch((exc) => { if (exc?.name !== "AbortError") primeraCarga.current = false; })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [page, busqueda, skusFiltro, contenedor, orden, soloPublicadosMl]);

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

  const seedVacia = (): Edicion => ({
    modo: "individual", piezas_por_caja: "",
    largo: "", ancho: "", alto: "", peso: "", costo_producto: "",
  });
  const setEdicion = (sku: string, campo: keyof Edicion, valor: string) =>
    setEdiciones((e) => ({ ...e, [sku]: { ...(e[sku] ?? seedVacia()), [campo]: valor } }));

  // Cálculo en vivo (CBM = vol×7500 MXN, costo = producto_MXN + CBM) mientras se
  // edita. costo_producto se ingresa en USD → se convierte a MXN con el TC.
  //
  // La columna "Costo unitario" es SIEMPRE la suma de las dos que tiene a la
  // izquierda — se esté editando el renglón o no. Hasta ahora esa garantía
  // vivía solo en el camino de edición: el renglón en reposo pintaba
  // `costos_finales.costo_unitario`, que es una FOTO guardada, y bastaba con
  // que esa tabla quedara vieja para que el renglón no cuadrara consigo mismo.
  //
  // Medido contra producción el 28-ago: 26 de 4,130 SKUs (0.6%) no cuadraban.
  // La mediana de la diferencia era CERO —casi todos coinciden al centavo— pero
  // los que fallaban lo hacían en grande: `TEC-0384-PLA`, validado por Brandon
  // en $88.00, se pintaba aquí en $1,265.38. Quien fije un precio leyendo eso
  // lo pone 14× más caro.
  //
  // `costo_unitario` queda de RESPALDO, solo para el renglón al que le falta
  // una de las dos piezas: ahí no hay suma que hacer y una foto vieja es mejor
  // que un hueco.
  function vivo(r: CostoRow) {
    const ed = ediciones[r.sku];
    if (!seleccion.has(r.sku) || !ed)
      return { cbm: r.costo_cbm,
               costo: sumaEnCentavos(r.costo_producto, r.costo_cbm, r.costo_unitario),
               prodMxn: r.costo_producto };
    // Siempre se calcula sobre la PIEZA: en modo "caja" se deriva primero.
    const pz = porPieza(ed);
    const l = pz.largo || null, a = pz.ancho || null, h = pz.alto || null;
    const cpMxn = prodMxnDe(r, ed, tcNum());
    const cbm = l && a && h ? Math.round((l * a * h) / 1_000_000 * TARIFA_CBM * 100) / 100 : r.costo_cbm;
    const costo = sumaEnCentavos(cpMxn, cbm, r.costo_unitario);
    return { cbm, costo, prodMxn: cpMxn };
  }

  /**
   * Abre "Validar costo de PUBLICADOS EN ML" con lo que esté seleccionado.
   *
   * NO se pre-filtra aquí a propósito. La selección sobrevive al cambio de
   * página y de filtros, y `rows` solo tiene las 50 visibles: filtrar con eso
   * dejaría fuera SKUs seleccionados en otra página sin decir nada. El modal
   * pregunta al backend cuáles están publicados y ENSEÑA los que quedan fuera
   * con su motivo antes de arrancar.
   */
  function abrirPublicados() {
    if (seleccion.size === 0) {
      setAvisoPublicados(
        "Marca primero las casillas de los SKUs que quieres validar. El proceso " +
          "corre sobre lo seleccionado, y únicamente sobre productos publicados " +
          "en Mercado Libre.",
      );
      return;
    }
    setAvisoPublicados(null);
    setPublicadosAbierto(true);
  }

  // Solo informativo, para el rótulo del botón: de los seleccionados que están
  // a la vista, cuántos NO tienen publicación en ML. `publicado_ml === null`
  // (el backend no pudo saberlo) no cuenta como "no publicado".
  const noPublicadosVisibles = useMemo(
    () => rows.filter((r) => seleccion.has(r.sku) && r.publicado_ml === false).length,
    [rows, seleccion],
  );

  async function regenerarBulk() {
    if (seleccion.size === 0 || bulkRun) return;
    setBulkRun(true);
    setBulkResult(null);
    try {
      const tc = tcNum();
      const items = [...seleccion].map((sku) => {
        const ed = ediciones[sku] ?? ({} as Edicion);
        const pz = porPieza(ed);
        const fila = rows.find((x) => x.sku === sku);
        return {
          sku,
          // Mismo camino que la columna en pesos: lo tecleado se convierte, lo
          // no tocado se conserva tal cual está guardado.
          costo_producto: fila
            ? prodMxnDe(fila, ed, tc)
            : (() => {
                const v = n(ed.costo_producto ?? "");
                return v != null ? Math.round(v * tc * 100) / 100 : null;
              })(),
          // Se guarda SIEMPRE la pieza: costos_validados es por unidad.
          largo: pz.largo || null,
          ancho: pz.ancho || null,
          alto: pz.alto || null,
          peso: pz.peso || null,
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
              {/* El tutorial vive del lado del TEXTO, no junto a los dos botones
                  de la derecha: es lo que se lee, no una tercera acción que
                  compita con ellos. Confundir "Validar" con "Regenerar" es el
                  riesgo real de esta pantalla, y esto es lo que lo explica. */}
              <button
                onClick={() => setComoAbierto(true)}
                className="mt-3 flex items-center gap-2 rounded-xl bg-white/15 px-3 py-2 text-xs font-semibold text-white ring-1 ring-white/30 backdrop-blur hover:bg-white/25"
              >
                <BookOpen size={14} /> Cómo validar costos
              </button>
            </div>
            <div className="text-right">
              <div className="text-4xl font-black tabular-nums">{new Intl.NumberFormat("es-MX").format(pag.total)}</div>
              <div className="text-xs font-semibold uppercase tracking-wide opacity-80">SKUs con costo</div>
              {/* Dos caminos al mismo destino, en direcciones opuestas:
                  · Resolver  → packing-list-primero (cargas el xlsx)
                  · Validar   → SKU-primero (partes de lo seleccionado)
                  El segundo lleva su alcance ESCRITO, no solo en el tooltip:
                  confundirlos es el riesgo real de esta pantalla. */}
              <div className="mt-2 flex flex-col items-end gap-2">
                <button
                  onClick={() => setResolverAbierto(true)}
                  title="Compara un packing list de contenedor contra estos costos"
                  className="flex items-center gap-2 rounded-xl bg-white/15 px-3 py-2 text-xs font-semibold text-white ring-1 ring-white/30 backdrop-blur hover:bg-white/25"
                >
                  <Sparkles size={14} /> Resolver desde packing list
                </button>
                <button
                  onClick={abrirPublicados}
                  title={
                    seleccion.size === 0
                      ? "Selecciona SKUs en la tabla: el proceso corre sobre lo seleccionado, y solo sobre lo publicado en Mercado Libre"
                      : "Le busca a cada SKU su renglón en el packing list — foto de Odoo, dHash y, si hace falta, la foto de la publicación de ML con IA"
                  }
                  className="flex items-center gap-2 rounded-xl bg-white/15 px-3 py-2 text-xs font-semibold text-white ring-1 ring-white/30 backdrop-blur hover:bg-white/25"
                >
                  <PackageSearch size={14} />
                  <span className="text-left leading-tight">
                    Validar costo desde packing list
                    <span className="block text-[10px] font-bold uppercase tracking-wide text-white/85">
                      solo productos publicados en Mercado Libre
                    </span>
                  </span>
                  {seleccion.size > 0 && (
                    <span className="rounded-full bg-white/25 px-1.5 py-0.5 text-[10px] font-bold tabular-nums">
                      {seleccion.size}
                    </span>
                  )}
                </button>
                {avisoPublicados && (
                  <p className="max-w-xs text-right text-[11px] leading-snug text-white/90">
                    {avisoPublicados}
                  </p>
                )}
              </div>
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
            {/* El filtro lo resuelve el backend con un `exists` contra
                channel.listings: "publicado" en ML lo decide `situacion`
                (active/paused), no el `status` de nuestro publicador. */}
            <button
              onClick={() => { setSoloPublicadosMl((v) => !v); setPage(1); }}
              title="Solo SKUs con publicación viva en Mercado Libre (activa o pausada) — que es el universo del botón de validar costo"
              className={[
                "flex items-center gap-1.5 rounded-lg border px-3 py-2 text-sm font-semibold transition-colors",
                soloPublicadosMl
                  ? "border-indigo-300 bg-indigo-50 text-indigo-700"
                  : "border-slate-200 bg-white text-slate-500 hover:bg-slate-50",
              ].join(" ")}
            >
              <ShoppingBag size={15} /> Solo publicados en ML
            </button>
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
                <th className="px-3 py-3 text-right font-semibold">Peso (kg)</th>
                {/* La frontera entre monedas cae aquí: la primera es la CAPTURA
                    en dólares, la de junto es esa misma cifra ya convertida —
                    que es la que se guarda y la que suma. */}
                <th className="bg-amber-50/40 px-3 py-3 text-right font-semibold">
                  <TituloMoneda moneda="USD">Costo prod.</TituloMoneda>
                  <div className={NOTA_TH}>lo que se le paga al proveedor</div>
                </th>
                <th className="bg-amber-50/20 px-3 py-3 text-right font-semibold">
                  <TituloMoneda moneda="MXN">Costo prod.</TituloMoneda>
                  <div className={NOTA_TH}>costo USD × tipo de cambio</div>
                </th>
                <th className="px-3 py-3 text-right font-semibold">
                  <TituloMoneda moneda="MXN">Flete CBM</TituloMoneda>
                  <div className={NOTA_TH}>volumen m³ × ${TARIFA_CBM.toLocaleString("es-MX")}</div>
                </th>
                <th className="px-3 py-3 text-right font-semibold">
                  <TituloMoneda moneda="MXN">Costo unitario</TituloMoneda>
                  <div className={NOTA_TH}>costo prod. MXN + flete CBM</div>
                </th>
              </tr>
            </thead>
            <tbody>
              {cargando || (rows.length === 0 && primeraCarga.current) ? (
                Array.from({ length: 10 }).map((_, i) => (
                  <tr key={i} className="border-b border-slate-100">
                    <td colSpan={9} className="px-4 py-4"><div className="h-5 w-full animate-pulse rounded bg-slate-100" /></td>
                  </tr>
                ))
              ) : rows.length === 0 ? (
                <tr><td colSpan={9} className="px-4 py-16 text-center text-slate-400">Sin resultados.</td></tr>
              ) : (
                rows.map((r) => {
                  const sel = seleccion.has(r.sku);
                  const ed = ediciones[r.sku];
                  const { cbm, costo, prodMxn } = vivo(r);
                  return (
                    <Fragment key={r.sku}>
                    <tr className={["border-b transition-colors", sel ? "border-transparent bg-indigo-50/50" : "border-slate-100 hover:bg-slate-50"].join(" ")}>
                      <td className="px-4 py-3">
                        <input type="checkbox" checked={sel} onChange={() => toggle(r.sku, r)} className="h-4 w-4 cursor-pointer accent-indigo-600" />
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          <div className="font-mono text-xs text-slate-500">{r.sku}</div>
                          {/* Capturar por caja master. Está aquí y no en una
                              pantalla aparte porque la conversión a pieza es
                              justo donde se equivoca la captura a mano: dividir
                              cada lado entre las piezas da volumen ÷ n³. */}
                          <button
                            onClick={() => { if (!sel) toggle(r.sku, r); setCajaMaster(r.sku); }}
                            title="Capturar desde la caja master y derivar la pieza"
                            className="rounded p-1 text-slate-300 hover:bg-indigo-50 hover:text-indigo-600"
                          >
                            <Box size={13} />
                          </button>
                          {/* Solo se pinta lo que SÍ está publicado. `null` es
                              "no se pudo saber" (el listado cayó al fallback
                              congelado), no "no está publicado": por eso no
                              lleva marca de ningún tipo. */}
                          {r.publicado_ml === true && (
                            <span
                              title="Publicado en Mercado Libre — entra en la validación de costo desde packing list"
                              className="rounded bg-amber-100 px-1 py-0.5 text-[9px] font-bold uppercase tracking-wide text-amber-700"
                            >
                              ML
                            </span>
                          )}
                          {/* El MISMO chip que pinta Análisis, no uno parecido:
                              ya hubo precedente de lo que cuesta duplicar una
                              etiqueta, y aquí importa que "validado" se lea
                              igual en las dos pantallas. */}
                          <ChipRevision
                            revisadoAt={r.revisado_at}
                            revisadoPor={r.revisado_por}
                            movida={r.revision_movida}
                          />
                        </div>
                        {r.nombre && <div className="line-clamp-1 max-w-[240px] text-xs text-slate-600">{r.nombre}</div>}
                      </td>
                      <td className="px-3 py-3 text-xs text-slate-500">{r.contenedor ?? "—"}</td>
                      {/* Dimensiones — editable al seleccionar */}
                      <td className="px-3 py-3 text-xs text-slate-600">
                        {sel && ed ? (
                          <div className="flex flex-col gap-1">
                            {/* Qué significa lo que se está tecleando. Sin esto
                                la fila no puede saber si 84×33×60 es la pieza
                                o el cartón, y el flete sale 100x mal. */}
                            <div className="flex w-fit rounded-md border border-slate-200 bg-slate-50 p-0.5 text-[10px] font-medium">
                              {(["individual", "caja"] as const).map((m) => (
                                <button
                                  key={m}
                                  type="button"
                                  onClick={() => setEdicion(r.sku, "modo", m)}
                                  className={`rounded px-2 py-0.5 transition ${
                                    ed.modo === m ? "bg-white text-indigo-700 shadow-sm" : "text-slate-500 hover:text-slate-700"
                                  }`}
                                >
                                  {m === "individual" ? "Individual" : "Caja master"}
                                </button>
                              ))}
                            </div>
                            <div className="flex items-center gap-1">
                              <CeldaInput value={ed.largo} onChange={(v) => setEdicion(r.sku, "largo", v)} />
                              <span className="text-slate-300">×</span>
                              <CeldaInput value={ed.ancho} onChange={(v) => setEdicion(r.sku, "ancho", v)} />
                              <span className="text-slate-300">×</span>
                              <CeldaInput value={ed.alto} onChange={(v) => setEdicion(r.sku, "alto", v)} />
                              {ed.modo === "caja" && (
                                <>
                                  <span className="ml-1 text-[10px] text-slate-400">÷</span>
                                  <CeldaInput
                                    value={ed.piezas_por_caja}
                                    onChange={(v) => setEdicion(r.sku, "piezas_por_caja", v)}
                                  />
                                  <span className="text-[10px] text-slate-400">pz</span>
                                </>
                              )}
                            </div>
                            {/* La derivación se muestra SIEMPRE que esté en modo
                                caja: es lo que de verdad se va a guardar. */}
                            {ed.modo === "caja" && (() => {
                              const pz = porPieza(ed);
                              if (!ed.piezas_por_caja.trim()) {
                                return <div className="text-[10px] text-amber-600">Falta piezas por caja</div>;
                              }
                              const vol = (pz.largo * pz.ancho * pz.alto) / 1_000_000;
                              const dens = vol > 0 && pz.peso ? pz.peso / vol : 0;
                              return (
                                <div className="text-[10px] leading-tight">
                                  <span className="font-mono text-indigo-700">
                                    {pz.largo}×{pz.ancho}×{pz.alto} cm
                                    {pz.peso ? ` · ${pz.peso} kg` : ""}
                                  </span>
                                  <span className="text-slate-400"> por pieza</span>
                                  {dens > 3000 && (
                                    <span className="ml-1 text-rose-600">· {Math.round(dens)} kg/m³ imposible</span>
                                  )}
                                </div>
                              );
                            })()}
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
                      {/* Costo producto en DÓLARES: es la captura. Va en
                          ámbar para que no se confunda con las columnas de
                          pesos que tiene a la derecha. */}
                      <td className="bg-amber-50/30 px-3 py-3 text-right text-slate-600">
                        {sel && ed
                          ? <EntradaMoneda moneda="USD" compacto alineado="right"
                                           value={ed.costo_producto}
                                           onChange={(v) => setEdicion(r.sku, "costo_producto", v)}
                                           titulo="En dólares — se guarda en pesos con el TC de la barra de abajo" />
                          : (r.costo_producto != null
                              ? <span className="font-medium text-amber-700">${mxnToUsd(r.costo_producto, tcNum())}</span>
                              : "—")}
                      </td>
                      {/* Costo producto en PESOS: la conversión de la celda
                          anterior con el TC de la barra de abajo. Es el número
                          que se guarda y el que suma al costo unitario, así que
                          se muestra en vez de dejarlo implícito. */}
                      <td className={["bg-amber-50/20 px-3 py-3 text-right", sel ? "font-medium text-indigo-600" : "text-slate-600"].join(" ")}>{precioMXN(prodMxn)}</td>
                      {/* Flete CBM + Costo unitario — en vivo si está seleccionado */}
                      <td className={["px-3 py-3 text-right", sel ? "font-semibold text-indigo-600" : "text-slate-600"].join(" ")}>{precioMXN(cbm)}</td>
                      <td className={["px-3 py-3 text-right font-semibold", sel ? "text-indigo-700" : "text-slate-800"].join(" ")}>{precioMXN(costo)}</td>
                    </tr>
                    {/* Desglose: de dónde sale el precio y qué se lleva cada
                        quien. Va en la misma línea —no en un panel aparte—
                        porque es lo que se mira mientras se teclea. */}
                    {sel && (
                      <tr className="border-b border-slate-100 bg-indigo-50/50">
                        <td />
                        <td colSpan={8} className="px-4 pb-3">
                          <Desglose calc={desglose[r.sku]} pendiente={!(r.sku in desglose)} />
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
            {/* Con esta tasa se convierte el costo capturado en dólares antes
                de guardarlo: si está mal, TODA la tanda queda mal. */}
            <label className="flex items-center gap-1.5 text-xs font-semibold text-slate-500"
                   title="Tipo de cambio con el que el costo en dólares se guarda en pesos">
              TC
              <ChipMoneda moneda="USD" />
              <span className="text-[9px] font-bold text-slate-400">→</span>
              <ChipMoneda moneda="MXN" />
              <input value={tcBulk} onChange={(e) => setTcBulk(e.target.value)} inputMode="decimal"
                className="w-16 rounded-lg border border-slate-200 px-2 py-1.5 text-sm tabular-nums text-slate-700 outline-none focus:ring-2" style={{ outlineColor: ACENTO }} />
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
            {/* Espejo del botón del banner: aquí es donde el usuario está
                mirando el contador de la selección. */}
            <button
              onClick={abrirPublicados}
              title={
                seleccion.size === 0
                  ? "Selecciona SKUs en la tabla. Solo se procesan los publicados en Mercado Libre."
                  : noPublicadosVisibles > 0
                    ? `${noPublicadosVisibles} de los seleccionados a la vista NO están publicados en ML: el modal te dirá cuáles quedan fuera antes de arrancar.`
                    : "Le busca a cada SKU publicado en ML su renglón en el packing list"
              }
              className="flex items-center gap-2 rounded-lg border border-indigo-200 bg-white px-4 py-2 text-sm font-semibold text-indigo-700 hover:bg-indigo-50"
            >
              <PackageSearch size={16} />
              Validar costo · publicados en ML ({seleccion.size})
            </button>
            <button onClick={regenerarBulk} disabled={seleccion.size === 0 || bulkRun}
              className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-bold text-white shadow-sm transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
              style={{ backgroundColor: COLOR }}>
              {bulkRun ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
              Regenerar y guardar ({seleccion.size})
            </button>
          </div>
        </div>
      </div>

      {comoAbierto && (
        <ComoValidarCostosModal onCerrar={() => setComoAbierto(false)} />
      )}

      {resolverAbierto && (
        <ResolverCostosModal onCerrar={() => setResolverAbierto(false)} />
      )}

      {publicadosAbierto && (
        <ValidarPublicadosModal
          skus={[...seleccion]}
          // La selección se limpia AL CERRAR, no al guardar. El modal se monta
          // con `skus={[...seleccion]}`, así que vaciarla a media sesión le
          // cambiaba la clave por debajo y lo reiniciaba al pronóstico: se
          // veía como si la ventana se cerrara sola justo después de escribir,
          // y se llevaba el resumen de lo que se saltó antes de poder leerlo.
          onCerrar={() => {
            setPublicadosAbierto(false);
            setSeleccion(new Set());
            setEdiciones({});
          }}
          // Guardar solo REFRESCA la tabla de atrás; la ventana se queda viva
          // para poder corregir los que no entraron.
          onGuardado={() => cargar()}
        />
      )}

      {cajaMaster && (
        <CajaMasterPanel
          sku={cajaMaster}
          tipoCambio={tcNum()}
          margen={(Number(margenBulk) || 0) / 100}
          incluirEnvio={envioBulk}
          onCerrar={() => setCajaMaster(null)}
          onAplicar={(d) => {
            // Los valores POR PIEZA se vuelcan en la edición inline: de ahí en
            // adelante el flujo es el de siempre (Regenerar y guardar).
            setEdiciones((e) => ({
              ...e,
              [cajaMaster]: {
                // Ya vienen convertidos a pieza: la fila los toma como individuales.
                modo: "individual", piezas_por_caja: "",
                largo: String(d.largo), ancho: String(d.ancho), alto: String(d.alto),
                peso: String(d.peso), costo_producto: String(d.costoUsd),
              },
            }));
          }}
        />
      )}
    </div>
  );
}

// Input compacto para editar una celda de la tabla.
/**
 * El renglón de desglose: los costos EN EL ORDEN EN QUE SE ACUMULAN, de lo que
 * se le paga al proveedor hasta lo que queda.
 *
 *   producto → flete → comisión → envío → costo final → margen
 *
 * Antes abría con el precio de venta y mezclaba rótulos ("Costo base" ya traía
 * el flete sumado adentro; "Precio base" era en realidad el de oferta), así que
 * las seis casillas no se podían sumar con la vista: no había cómo saber de
 * dónde salía el costo final ni contra qué se sacaba el margen.
 *
 * Por eso debajo van las dos fórmulas ESCRITAS CON LOS NÚMEROS de las casillas,
 * no con símbolos. Todo lo que se muestra tiene que poder comprobarse sumando
 * lo que se ve; la única cifra que no tiene casilla propia —el IVA— se nombra
 * igual, en la nota y en la fórmula.
 *
 * Los números vienen del backend (`/preview`), incluida la comisión de la
 * categoría real de ML.
 */
function Desglose({ calc, pendiente }: { calc: CostoCalculo | null | undefined; pendiente: boolean }) {
  if (pendiente) {
    return (
      <div className="flex items-center gap-2 text-[11px] text-slate-400">
        <Loader2 size={12} className="animate-spin" /> calculando desglose…
      </div>
    );
  }
  if (!calc) {
    // El backend devuelve vacío cuando NO tiene una comisión confiable (sin
    // token de ML y sin histórico de la categoría). Es la causa habitual, y
    // tiene salida: escribir el % en la barra de abajo.
    return (
      <div className="text-[11px] text-slate-400">
        Sin desglose — falta costo, dimensiones o una comisión confiable
        (escribe el % de comisión en la barra de abajo).
      </div>
    );
  }
  const producto = calc.costo_producto ?? 0;
  const flete = calc.costo_cbm ?? 0;
  // El IVA no lleva casilla propia —son seis costos, no siete— pero SÍ está
  // dentro del costo final. Se nombra en la nota y en la fórmula para que la
  // suma cierre: callarlo dejaba cientos de pesos sin explicar.
  const costoFinal = Math.round(
    (producto + flete + calc.costo_comision + calc.costo_fee_envio + calc.iva_mnt) * 100,
  ) / 100;
  // Se recalcula con las cifras YA REDONDEADAS que están a la vista, en vez de
  // usar `ganancia_neta` del backend: son el mismo número, pero así la fórmula
  // de abajo cuadra al centavo con lo que el usuario lee.
  const precio = calc.precio_sugerido;
  const neto = Math.round((precio - costoFinal) * 100) / 100;
  const margen = precio ? neto / precio : 0;
  const tonoMargen =
    margen <= 0 ? "text-rose-600" : margen < 0.15 ? "text-amber-600" : "text-emerald-600";

  const celdas: { rotulo: string; valor: string; tono?: string; nota?: string }[] = [
    { rotulo: "1 · Costo producto", valor: precioMXN(producto),
      nota: "lo pagado al proveedor, en pesos" },
    { rotulo: "2 · Costo flete", valor: precioMXN(flete),
      nota: `${calc.volumen_m3 ?? 0} m³ × $${calc.tarifa_cbm_m3.toLocaleString("es-MX")}` },
    { rotulo: "3 · Comisión ML", valor: precioMXN(calc.costo_comision),
      nota: `${(calc.pct_comision * 100).toFixed(1)}% de la categoría${calc.comision_estimada ? " · estimada" : ""}` },
    { rotulo: "4 · Envío real", valor: precioMXN(calc.costo_fee_envio),
      nota: calc.incluir_envio ? "tarifa ML por peso y precio" : "no se suma" },
    { rotulo: "5 · Costo final", valor: precioMXN(costoFinal), tono: "text-slate-900",
      nota: `suma de 1 a 4 + IVA ${precioMXN(calc.iva_mnt)}` },
    { rotulo: "6 · Margen neto", valor: `${(margen * 100).toFixed(1)}%`, tono: tonoMargen,
      nota: `${precioMXN(neto)} netos por pieza` },
  ];

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-stretch gap-1.5">
        {celdas.map((c) => (
          <div key={c.rotulo} className="min-w-[124px] flex-1 rounded-lg border border-indigo-100 bg-white px-2.5 py-1.5">
            <div className="text-[9px] font-semibold uppercase tracking-wide text-slate-400">{c.rotulo}</div>
            <div className={`font-mono text-xs font-semibold ${c.tono ?? "text-slate-700"}`}>{c.valor}</div>
            {c.nota && <div className="truncate text-[9px] text-slate-400" title={c.nota}>{c.nota}</div>}
          </div>
        ))}
      </div>

      {/* Las dos fórmulas, con los números de las casillas de arriba puestos en
          su lugar. Es la diferencia entre "confía en el 21.4%" y "míralo". */}
      <ul className="space-y-0.5 text-[10px] leading-relaxed text-slate-500">
        <li>
          <span className="text-slate-400">•</span>{" "}
          <span className="font-semibold text-slate-600">Costo final</span>
          {" = "}
          <span className="font-mono">
            {precioMXN(producto)} + {precioMXN(flete)} + {precioMXN(calc.costo_comision)}
            {" + "}
            {precioMXN(calc.costo_fee_envio)} + {precioMXN(calc.iva_mnt)}
            {" = "}
            <span className="font-semibold text-slate-800">{precioMXN(costoFinal)}</span>
          </span>{" "}
          <span className="text-slate-400">(producto + flete + comisión + envío + IVA)</span>
        </li>
        <li>
          <span className="text-slate-400">•</span>{" "}
          <span className="font-semibold text-slate-600">Margen neto</span>
          {" = ("}
          <span className="font-mono">
            {precioMXN(precio)} − {precioMXN(costoFinal)}
          </span>
          {") ÷ "}
          <span className="font-mono">{precioMXN(precio)}</span>
          {" = "}
          <span className={`font-mono font-semibold ${tonoMargen}`}>{(margen * 100).toFixed(1)}%</span>{" "}
          <span className="text-slate-400">
            (precio de venta en ML menos el costo final, sobre el precio)
          </span>
        </li>
      </ul>

      {calc.comision_estimada && (
        <div className="flex items-center gap-1 text-[10px] text-amber-600">
          <AlertTriangle size={11} /> comisión de respaldo: sin categoría de ML
        </div>
      )}
    </div>
  );
}

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
