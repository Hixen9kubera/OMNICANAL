"use client";

/**
 * INVENTARIO · Catálogo Maestro
 *
 * Sigue el diseño 1b (tabla densa) de Claude Design: banner, cinco KPIs, banda
 * de alertas que filtra, barra de herramientas y tabla. El cajón lateral es la
 * 1d (ficha del SKU) y su bloque de movimientos, la 1f (trazabilidad).
 *
 * La carpeta ES la ruta; `SesionGuard` ya lo monta `app/layout.tsx`, así que
 * aquí NO va — pero `AppNavbar` sí, porque el layout no lo pinta.
 *
 * DOS COSAS DEL DISEÑO QUE NO SE COPIARON LITERAL, Y POR QUÉ:
 *
 * 1. El banner del diseño dice «La bodega es la fuente de verdad» y «Odoo: solo
 *    lectura histórica». Hoy es al revés y está medido: `stock_watch` copia el
 *    `free_qty` de Odoo a Woo cada pasada, y el libro de Odoo reproduce ese
 *    saldo en el 100% de una muestra de 150 SKUs. Rotularlo como dice el diseño
 *    sería escribir en pantalla lo contrario de lo que hace el sistema, así que
 *    el banner dice lo que ES hoy. La frase del diseño describe el destino, y
 *    se pondrá el día que la cadena se invierta de verdad.
 *
 * 2. «Conteo físico» y «Entrada por packing list» ESCRIBEN stock. Van pintados
 *    porque son parte del diseño, pero deshabilitados y diciendo qué falta: un
 *    ajuste humano hoy se revertiría solo en ≤20 min y la bitácora culparía a
 *    Odoo de haberlo borrado. Necesitan la decisión de precedencia de Brandon.
 *
 * Y tres cosas que esta pantalla tiene PROHIBIDO decir, porque serían mentira:
 * «en tránsito» (son recepciones vencidas sin validar), «publicado» a secas de
 * una variación cuyo padre está en borrador, y un solo número de contenedor
 * cuando las dos fuentes discrepan.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, ArrowLeftRight, ArrowUpDown, Boxes, Camera, CheckCircle2,
  ChevronRight, ClipboardList, Clock, Container, Database, Download, EyeOff,
  History, Layers, Loader2, Lock, MapPin, Package, PackagePlus, PackageX,
  PackageSearch, RefreshCw, RotateCcw, ShieldAlert, Ship, ShoppingCart, Truck,
  X,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import { listarInventario, mensajeDeError, movimientosInventario } from "@/lib/api";
import type {
  ClaveEtapa, Cuadre, EstadoEtapa, FilaInventario, InventarioResp, Movimiento,
  MovimientosResp,
} from "@/lib/types";

const ETAPAS: { clave: ClaveEtapa; titulo: string; icono: typeof Camera }[] = [
  { clave: "en_proceso", titulo: "En proceso", icono: Clock },
  { clave: "fotos", titulo: "Fotos", icono: Camera },
  { clave: "variantes", titulo: "Variantes", icono: Layers },
  { clave: "validado", titulo: "Validado", icono: CheckCircle2 },
  { clave: "enviado_full", titulo: "Enviado", icono: Truck },
];

const ESTILO_ETAPA: Record<EstadoEtapa, string> = {
  listo: "border-emerald-200 bg-emerald-50 text-emerald-700",
  parcial: "border-amber-200 bg-amber-50 text-amber-700",
  pendiente: "border-slate-200 bg-slate-50 text-slate-400",
  bloqueado: "border-rose-200 bg-rose-50 text-rose-700",
  na: "border-slate-200 bg-white text-slate-300",
};

const ESTILO_CUADRE: Record<Cuadre["estado"], string> = {
  ok: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  aviso: "bg-amber-50 text-amber-800 ring-amber-200",
  peligro: "bg-rose-50 text-rose-700 ring-rose-200",
  neutro: "bg-slate-100 text-slate-500 ring-slate-200",
};

const CAUSAS = [
  { v: "reales", t: "Todo" },
  { v: "entrada", t: "Entradas" },
  { v: "venta", t: "Ventas" },
  { v: "envio_full", t: "FULL / FBA" },
  { v: "devolucion", t: "Devoluciones" },
  { v: "ajuste", t: "Ajustes de conteo" },
  { v: "traspaso", t: "Traspasos" },
  { v: "merma", t: "Merma" },
  { v: "todo", t: "Con pasos internos" },
];

const ETIQUETA_CAUSA: Record<string, string> = {
  entrada: "Entrada", venta: "Venta", envio_full: "FULL / FBA",
  devolucion: "Devolución", ajuste: "Ajuste", traspaso: "Traspaso",
  preparacion: "Preparación", merma: "Merma", cuarentena: "Cuarentena",
  otro: "Otro",
};

const COLOR_CAUSA: Record<string, string> = {
  entrada: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  devolucion: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  venta: "bg-rose-50 text-rose-700 ring-rose-200",
  envio_full: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  ajuste: "bg-amber-50 text-amber-800 ring-amber-200",
  traspaso: "bg-sky-50 text-sky-700 ring-sky-200",
  preparacion: "bg-slate-50 text-slate-400 ring-slate-200",
  merma: "bg-rose-50 text-rose-700 ring-rose-200",
  cuarentena: "bg-rose-50 text-rose-700 ring-rose-200",
};

/** El icono de cada tipo de movimiento — la columna de la izquierda de la 1f. */
const ICONO_CAUSA: Record<string, typeof Package> = {
  entrada: PackagePlus,
  venta: ShoppingCart,
  envio_full: Truck,
  devolucion: RotateCcw,
  ajuste: ClipboardList,
  traspaso: ArrowLeftRight,
  preparacion: Package,
  merma: PackageX,
  cuarentena: ShieldAlert,
  otro: Package,
};

/** Ventanas de la pantalla de trazabilidad. `null` = todo el histórico. */
const VENTANAS = [
  { v: 30, t: "Últimos 30 días" },
  { v: 90, t: "Últimos 90 días" },
  { v: 365, t: "Último año" },
  { v: 0, t: "Todo el histórico" },
];

const ORDENES = [
  { v: "piezas", t: "Piezas: mayor a menor" },
  { v: "piezas_asc", t: "Piezas: menor a mayor" },
  { v: "sku", t: "SKU (A-Z)" },
  { v: "atencion", t: "Requieren atención primero" },
];

const POR_PAGINA = 40;

const num = (v: number | null | undefined, guion = "—") =>
  v === null || v === undefined ? guion : Math.round(v).toLocaleString("es-MX");

/** «hace 4 min», como el banner del diseño. */
function haceCuanto(iso: string | null): string {
  if (!iso) return "sin registro";
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "sin registro";
  const min = Math.floor(ms / 60000);
  if (min < 1) return "hace segundos";
  if (min < 60) return `hace ${min} min`;
  const h = Math.floor(min / 60);
  if (h < 24) return `hace ${h} h`;
  return `hace ${Math.floor(h / 24)} d`;
}

export default function InventarioPage() {
  const [datos, setDatos] = useState<InventarioResp | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [busqueda, setBusqueda] = useState("");
  const [skusInput, setSkusInput] = useState("");
  const [filtroSkus, setFiltroSkus] = useState<string[] | undefined>(undefined);
  const [bodega, setBodega] = useState("");
  const [orden, setOrden] = useState("piezas");
  const [alerta, setAlerta] = useState<string | null>(null);
  const [pagina, setPagina] = useState(1);
  // Dos capas distintas a propósito, como pidió Brandon: el CAJÓN es la ficha
  // del SKU con un RESUMEN de movimientos; el icono ⇅ abre la pantalla COMPLETA
  // de trazabilidad. Se puede llegar a la segunda desde la tabla sin pasar por
  // la primera, o desde el botón «Movimiento» de la ficha.
  const [abierto, setAbierto] = useState<FilaInventario | null>(null);
  const [traza, setTraza] = useState<FilaInventario | null>(null);

  useEffect(() => {
    const t = setTimeout(() => {
      const l = skusInput.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
      setFiltroSkus(l.length ? l : undefined);
      setPagina(1);
    }, 500);
    return () => clearTimeout(t);
  }, [skusInput]);

  const cargar = useCallback(() => {
    const ctrl = new AbortController();
    setCargando(true);
    setError(null);
    listarInventario(filtroSkus, ctrl.signal)
      .then(setDatos)
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        // /costos se traga los errores y la tabla queda vacía sin decir por qué
        // — el síntoma exacto de los tres 403 de RBAC. Aquí se muestran.
        setError(mensajeDeError(e, "No se pudo leer el inventario."));
      })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [filtroSkus]);

  useEffect(() => cargar(), [cargar]);

  const filtradas = useMemo(() => {
    let items = datos?.items ?? [];
    if (alerta) {
      const pruebas: Record<string, (f: FilaInventario) => boolean> = {
        sin_alta: (f) => !f.existe_en_woo,
        sin_odoo: (f) => !f.existe_en_odoo,
        invisibles: (f) => f.invisible_en_tienda,
        activo_sin_stock: (f) => f.cuadre.etiqueta === "Activo sin stock",
        descuadre: (f) => !!f.descuadre,
        recepcion_vencida: (f) => (f.recepcion_dias ?? 0) > 30,
        sin_fotos: (f) => f.etapas.fotos.estado === "pendiente",
        sin_costo: (f) => f.etapas.validado.estado === "pendiente",
        contenedor_discrepa: (f) => f.contenedor_discrepa,
        contenedor_no_comparable: (f) => f.contenedor_no_comparable,
        odoo_duplicado: (f) => f.odoo_duplicado,
      };
      items = items.filter(pruebas[alerta] ?? (() => true));
    }
    if (busqueda.trim()) {
      const q = busqueda.trim().toLowerCase();
      items = items.filter(
        (f) => f.sku.toLowerCase().includes(q) || f.nombre.toLowerCase().includes(q));
    }
    if (bodega) items = items.filter((f) => f.bodegas.includes(bodega));

    const orden_: Record<string, (a: FilaInventario, b: FilaInventario) => number> = {
      piezas: (a, b) => (b.stock_odoo ?? -1) - (a.stock_odoo ?? -1),
      piezas_asc: (a, b) => (a.stock_odoo ?? Infinity) - (b.stock_odoo ?? Infinity),
      sku: (a, b) => a.sku.localeCompare(b.sku),
      // «Requieren atención» ordena por gravedad de la píldora de cuadre, que
      // es el mismo criterio con el que se pinta: rojo arriba.
      atencion: (a, b) => {
        const p = { peligro: 0, aviso: 1, neutro: 2, ok: 3 } as const;
        return p[a.cuadre.estado] - p[b.cuadre.estado];
      },
    };
    return [...items].sort(orden_[orden] ?? orden_.piezas);
  }, [datos, alerta, busqueda, bodega, orden]);

  const totalPaginas = Math.max(1, Math.ceil(filtradas.length / POR_PAGINA));
  const pag = Math.min(pagina, totalPaginas);
  const visibles = filtradas.slice((pag - 1) * POR_PAGINA, pag * POR_PAGINA);
  const r = datos?.resumen;

  return (
    <div className="min-h-screen bg-[#f6f7fb]">
      <AppNavbar />
      <main className="mx-auto max-w-[1400px] px-4 py-6">
        <Banner resumen={r} esPiloto={datos?.es_piloto ?? true}
                cargando={cargando} onRecargar={cargar} />

        {error && (
          <div className="mt-4 flex items-start gap-2 rounded-xl bg-rose-50 p-3 text-sm text-rose-700 ring-1 ring-rose-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {r && <Kpis resumen={r} />}
        {r && (
          <BandaAlertas resumen={r} activa={alerta}
                        onToggle={(k) => { setAlerta((a) => (a === k ? null : k)); setPagina(1); }} />
        )}

        <Herramientas
          busqueda={busqueda} setBusqueda={(v) => { setBusqueda(v); setPagina(1); }}
          skusInput={skusInput} setSkusInput={setSkusInput}
          bodega={bodega} setBodega={(v) => { setBodega(v); setPagina(1); }}
          bodegas={r?.bodegas ?? []}
          orden={orden} setOrden={setOrden}
          hayFiltroSkus={!!filtroSkus}
        />

        <Tabla filas={visibles} cargando={cargando} onAbrir={setAbierto}
               onTraza={setTraza} />

        <Paginacion pagina={pag} total={totalPaginas} skus={filtradas.length}
                    onPagina={setPagina} />

        <p className="mt-4 text-xs leading-relaxed text-slate-400">
          Todo se lee en vivo de WooCommerce, Odoo y kubera en cada carga — nada
          sale de caché. Esta pestaña no escribe stock en ninguna parte.
        </p>
      </main>

      {abierto && (
        <Cajon fila={abierto} onCerrar={() => setAbierto(null)}
               onTraza={() => setTraza(abierto)} />
      )}
      {traza && <Trazabilidad fila={traza} onCerrar={() => setTraza(null)} />}
    </div>
  );
}

/* ────────────────────────────── el banner (1b) ────────────────────────────── */

function Banner({
  resumen, esPiloto, cargando, onRecargar,
}: {
  resumen?: InventarioResp["resumen"];
  esPiloto: boolean;
  cargando: boolean;
  onRecargar: () => void;
}) {
  const empuje = resumen?.ultimo_empuje;
  const pastilla =
    "inline-flex items-center gap-1.5 rounded-lg bg-white/15 px-2.5 py-1 text-[11px] font-semibold text-white/90 backdrop-blur-sm";
  return (
    <section className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-indigo-600 via-indigo-600 to-violet-700 px-6 py-5 text-white shadow-[0_2px_8px_rgba(79,70,229,.25)]">
      <div className="pointer-events-none absolute -right-16 -top-20 h-64 w-64 rounded-full bg-white/10" />
      <div className="relative flex flex-wrap items-start justify-between gap-6">
        <div className="min-w-0">
          <p className="text-[11px] font-bold uppercase tracking-[0.08em] text-white/70">
            Catálogo maestro · Bodega Kubera
          </p>
          <h1 className="mt-1 flex items-center gap-2.5 text-3xl font-extrabold tracking-tight">
            Inventario
            {esPiloto && (
              <span className="rounded-full bg-white/20 px-2.5 py-1 text-xs font-bold">
                piloto · 10 SKUs
              </span>
            )}
          </h1>
          {/* El diseño dice aquí «La bodega es la fuente de verdad». Hoy no lo
              es y está medido; el rótulo dice lo que el sistema HACE. */}
          <p className="mt-1.5 max-w-2xl text-sm text-white/80">
            Odoo es el maestro del inventario y su libro de movimientos es la
            única trazabilidad que existe. Esta vista lo lee — no lo modifica.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className={pastilla}>
              <ArrowUpDown className="h-3.5 w-3.5" />
              Odoo → Woo → ML · Amazon · TikTok
            </span>
            <span className={pastilla}>
              <Clock className="h-3.5 w-3.5" />
              Último empuje: {haceCuanto(empuje?.cuando ?? null)}
              {empuje?.escrituras ? ` · ${num(empuje.escrituras)} escrituras 24 h` : ""}
            </span>
            <span className={pastilla}>
              <Database className="h-3.5 w-3.5" />
              {num(empuje?.skus ?? null, "—")} SKUs vigilados
            </span>
          </div>
        </div>

        <div className="flex items-start gap-4">
          {resumen && (
            <div className="text-right">
              <div className="text-4xl font-extrabold leading-none tracking-tight tabular-nums">
                {num(resumen.disponible, "0")}
              </div>
              <div className="mt-1 text-[11px] font-bold uppercase tracking-[0.06em] text-white/70">
                piezas · {num(resumen.skus)} SKUs
              </div>
              <div className="mt-2 text-xs text-white/80">
                {resumen.completos} de {resumen.skus} con las 4 etapas cerradas
              </div>
            </div>
          )}
          <button
            type="button"
            onClick={onRecargar}
            disabled={cargando}
            title="Volver a cruzar WooCommerce, Odoo y kubera"
            className="rounded-lg bg-white/15 p-2 text-white backdrop-blur-sm transition hover:bg-white/25 disabled:opacity-50"
          >
            <RefreshCw className={`h-4 w-4 ${cargando ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>
    </section>
  );
}

function Kpis({ resumen }: { resumen: InventarioResp["resumen"] }) {
  const tarjetas = [
    { t: "Disponible", v: resumen.disponible, p: "libre de venta en Odoo", i: Boxes, tono: "" },
    { t: "Reservado", v: resumen.reservado, p: "comprometido en pedidos", i: Layers, tono: "" },
    {
      t: "En recepción", v: resumen.en_recepcion, i: Ship, tono: "aviso",
      p: resumen.alertas.recepcion_vencida
        ? `${resumen.alertas.recepcion_vencida} recepciones vencidas`
        : "recepciones abiertas",
    },
    { t: "FULL / FBA", v: resumen.full + resumen.fba, p: "bodega del marketplace", i: Truck, tono: "" },
    {
      t: "Descuadres", v: resumen.alertas.descuadre + resumen.alertas.activo_sin_stock,
      i: AlertTriangle, p: "Woo ≠ físico, o activo sin stock",
      tono: resumen.alertas.descuadre + resumen.alertas.activo_sin_stock ? "peligro" : "",
    },
  ];
  const marco = (tono: string) =>
    tono === "aviso" ? "border-amber-200 bg-amber-50"
      : tono === "peligro" ? "border-rose-200 bg-rose-50"
        : "border-slate-200 bg-white shadow-[0_1px_3px_rgba(16,24,40,.06)]";
  const cifra = (tono: string) =>
    tono === "aviso" ? "text-amber-800" : tono === "peligro" ? "text-rose-800" : "text-slate-900";

  return (
    <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
      {tarjetas.map((c) => (
        <div key={c.t} className={`rounded-2xl border p-4 ${marco(c.tono)}`}>
          <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
            <c.i className="h-3.5 w-3.5" />
            {c.t}
          </div>
          <div className={`mt-2 text-[28px] font-extrabold leading-none tracking-tight tabular-nums ${cifra(c.tono)}`}>
            {num(c.v, "0")}
          </div>
          <div className="mt-1 text-xs text-slate-500">{c.p}</div>
        </div>
      ))}
    </div>
  );
}

function BandaAlertas({
  resumen, activa, onToggle,
}: {
  resumen: InventarioResp["resumen"];
  activa: string | null;
  onToggle: (k: string) => void;
}) {
  const chips = [
    { k: "activo_sin_stock", t: "Sin stock y ACTIVO en canal", n: resumen.alertas.activo_sin_stock, tono: "peligro" },
    { k: "invisibles", t: "Publicada pero invisible", n: resumen.alertas.invisibles, tono: "peligro" },
    { k: "descuadre", t: "Descuadre Woo ↔ físico", n: resumen.alertas.descuadre, tono: "peligro" },
    { k: "sin_alta", t: "Sin alta en Woo", n: resumen.alertas.sin_alta, tono: "peligro" },
    { k: "odoo_duplicado", t: "Duplicado en Odoo", n: resumen.alertas.odoo_duplicado, tono: "peligro" },
    { k: "recepcion_vencida", t: "Recepción vencida", n: resumen.alertas.recepcion_vencida, tono: "aviso" },
    { k: "sin_fotos", t: "Sin fotos", n: resumen.alertas.sin_fotos, tono: "aviso" },
    { k: "sin_costo", t: "Sin costo", n: resumen.alertas.sin_costo, tono: "aviso" },
    { k: "contenedor_discrepa", t: "Contenedor discrepa", n: resumen.alertas.contenedor_discrepa, tono: "aviso" },
    { k: "contenedor_no_comparable", t: "Contenedor sin cotejar", n: resumen.alertas.contenedor_no_comparable, tono: "" },
    { k: "sin_odoo", t: "Sin producto en Odoo", n: resumen.alertas.sin_odoo, tono: "" },
  ].filter((c) => c.n > 0);

  if (!chips.length) return null;

  return (
    <div className="mt-4 flex flex-wrap items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3">
      <span className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
        Requiere atención
      </span>
      {chips.map((c) => {
        const on = activa === c.k;
        const base =
          c.tono === "peligro" ? "border-rose-200 bg-rose-50 text-rose-700"
            : c.tono === "aviso" ? "border-amber-200 bg-amber-50 text-amber-800"
              : "border-slate-200 bg-white text-slate-600";
        return (
          <button
            key={c.k} type="button" onClick={() => onToggle(c.k)}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-bold transition ${base} ${
              on ? "ring-2 ring-indigo-400 ring-offset-1" : "hover:brightness-95"}`}
          >
            {c.t} · {c.n}
          </button>
        );
      })}
      <span className="ml-auto text-xs text-slate-400">
        Cada chip filtra la tabla; ninguno la reemplaza.
      </span>
    </div>
  );
}

/* ─────────────────────── barra de herramientas (1b) ─────────────────────── */

function Herramientas({
  busqueda, setBusqueda, skusInput, setSkusInput, bodega, setBodega, bodegas,
  orden, setOrden, hayFiltroSkus,
}: {
  busqueda: string; setBusqueda: (v: string) => void;
  skusInput: string; setSkusInput: (v: string) => void;
  bodega: string; setBodega: (v: string) => void; bodegas: string[];
  orden: string; setOrden: (v: string) => void;
  hayFiltroSkus: boolean;
}) {
  const campo =
    "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600 outline-none focus:border-indigo-300";
  return (
    <div className="mt-4 flex flex-wrap items-center gap-2">
      <div className="relative">
        <PackageSearch className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
        <input
          value={busqueda} onChange={(e) => setBusqueda(e.target.value)}
          placeholder="SKU o nombre…"
          className={`${campo} w-56 pl-9`}
        />
      </div>
      <input
        value={skusInput} onChange={(e) => setSkusInput(e.target.value)}
        placeholder="Filtrar SKUs: TEC-0001, ORG-0885…"
        title="Trae del backend exactamente estos SKUs. Vacío = los 10 del piloto."
        className={`${campo} w-72`}
      />
      <select value={bodega} onChange={(e) => setBodega(e.target.value)} className={campo}>
        <option value="">Bodega: todas</option>
        {bodegas.map((b) => <option key={b} value={b}>Bodega: {b}</option>)}
      </select>
      <select value={orden} onChange={(e) => setOrden(e.target.value)} className={campo}>
        {ORDENES.map((o) => <option key={o.v} value={o.v}>{o.t}</option>)}
      </select>
      {hayFiltroSkus && (
        <button
          type="button" onClick={() => setSkusInput("")}
          className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-500 hover:bg-slate-50"
        >
          Volver al piloto
        </button>
      )}

      <div className="ml-auto flex items-center gap-2">
        {/* Los dos botones que ESCRIBEN stock. Van pintados porque son del
            diseño, y deshabilitados porque hoy un ajuste humano se revierte
            solo en ≤20 min. Ver la cabecera del archivo. */}
        <BotonBloqueado
          icono={Boxes} texto="Conteo físico"
          razon="Escribe stock. Falta decidir quién gana cuando una persona y Odoo dicen números distintos: hoy stock_watch copia el free_qty de Odoo cada pasada y borraría el ajuste en ≤20 min."
        />
        <BotonBloqueado
          icono={Ship} texto="Entrada por packing list" primario
          razon="Escribe stock y enciende un flujo vivo (el fan-out empuja a los cinco canales). Necesita el dale de Brandon y la decisión de precedencia."
        />
        <button
          type="button" disabled
          title="Pendiente: el CSV sale cuando el catálogo completo esté paginado en el backend."
          className="flex cursor-not-allowed items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-300"
        >
          <Download className="h-4 w-4" /> Exportar
        </button>
      </div>
    </div>
  );
}

function BotonBloqueado({
  icono: Icono, texto, razon, primario,
}: { icono: typeof Boxes; texto: string; razon: string; primario?: boolean }) {
  return (
    <button
      type="button" disabled title={`Bloqueado — ${razon}`}
      className={`flex cursor-not-allowed items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-semibold ${
        primario
          ? "bg-indigo-300 text-white"
          : "border border-slate-200 bg-white text-slate-300"}`}
    >
      <Icono className="h-4 w-4" /> {texto}
    </button>
  );
}

/* ────────────────────────────── la tabla (1b) ────────────────────────────── */

function Tabla({
  filas, cargando, onAbrir, onTraza,
}: {
  filas: FilaInventario[]; cargando: boolean;
  onAbrir: (f: FilaInventario) => void;
  onTraza: (f: FilaInventario) => void;
}) {
  return (
    <div className="mt-3 overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-[0_1px_3px_rgba(16,24,40,.06)]">
      <table className="w-full min-w-[1200px] text-sm">
        <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-400">
          <tr>
            <th className="w-14 px-3 py-3" />
            <th className="px-3 py-3 text-left font-bold">SKU · producto</th>
            <th className="px-3 py-3 text-left font-bold">Empaque</th>
            <th className="px-3 py-3 text-right font-bold">Cajas</th>
            <th className="px-3 py-3 text-right font-bold">Piezas</th>
            <th className="px-3 py-3 text-right font-bold">Reserv.</th>
            <th className="px-3 py-3 text-left font-bold">Ubicación</th>
            <th className="px-3 py-3 text-left font-bold">Woo ↔ físico</th>
            <th className="px-3 py-3 text-left font-bold">Etapas</th>
            <th className="px-3 py-3 text-right font-bold">Trazabilidad</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {cargando && !filas.length && (
            <tr>
              <td colSpan={10} className="px-4 py-16 text-center text-slate-400">
                <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                <p className="mt-2 text-sm">Cruzando WooCommerce, Odoo y kubera en vivo…</p>
              </td>
            </tr>
          )}
          {!cargando && !filas.length && (
            <tr>
              <td colSpan={10} className="px-4 py-16 text-center text-sm text-slate-400">
                Sin resultados.
              </td>
            </tr>
          )}
          {filas.map((f) => (
            <Fila key={f.sku} f={f} onAbrir={onAbrir} onTraza={onTraza} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Fila({
  f, onAbrir, onTraza,
}: {
  f: FilaInventario;
  onAbrir: (f: FilaInventario) => void;
  onTraza: (f: FilaInventario) => void;
}) {
  const piezas = f.stock_odoo;
  return (
    <tr className="group cursor-pointer align-middle hover:bg-slate-50/70" onClick={() => onAbrir(f)}>
      <td className="px-3 py-2.5">
        {f.imagen ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={f.imagen} alt="" loading="lazy"
               className="h-11 w-11 rounded-lg border border-slate-200 object-cover" />
        ) : (
          <div className="flex h-11 w-11 items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50">
            <Camera className="h-4 w-4 text-slate-300" />
          </div>
        )}
      </td>

      <td className="max-w-[290px] px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-[12px] font-bold text-slate-900">{f.sku}</span>
          {f.es_padre && (
            <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-bold text-indigo-600">
              {f.n_hijas} variantes
            </span>
          )}
          {f.tipo === "variacion" && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold text-slate-500">
              variante
            </span>
          )}
          {f.invisible_en_tienda && (
            <span title={`Publicada, pero su padre está en ${f.padre_status}: no se ve en la tienda`}
                  className="inline-flex items-center gap-1 rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-bold text-rose-700">
              <EyeOff className="h-3 w-3" /> invisible
            </span>
          )}
        </div>
        <div className="truncate text-xs text-slate-500">{f.nombre || "—"}</div>
      </td>

      {/* EMPAQUE: el factor arriba, el contenedor abajo. Los dos de ODOO —
          el packing list ya no manda aquí (Brandon, 4-sep). */}
      <td className="px-3 py-2.5">
        <div className="text-xs font-bold text-slate-700">
          {f.piezas_por_caja === null ? "sin factor" : `${f.piezas_por_caja} pzs/caja`}
        </div>
        <div className="flex items-center gap-1 text-[10px] text-slate-400">
          {f.contenedor ? (
            <>
              <Container className="h-3 w-3 shrink-0" />
              <span className="font-mono">{f.contenedor}</span>
              {f.embarque && <span>· emb. {f.embarque}</span>}
              {f.contenedor_es_booking && (
                <span title="Es la referencia del transitario, no un contenedor ISO">· booking</span>
              )}
              {f.contenedor_fuente === "costos_validados" && (
                <span className="text-slate-400"
                      title="Odoo no tiene contenedor para este SKU; se muestra el de costos_validados">
                  · de costos
                </span>
              )}
              {f.contenedor_discrepa && (
                <span className="text-rose-600"
                      title={`Odoo: ${f.contenedor_odoo} · costos: ${f.contenedor_costo}`}>
                  · discrepa
                </span>
              )}
              {f.contenedor_no_comparable && (
                <span
                  className="text-amber-700"
                  title={`Sin cotejar: Odoo dice "${f.contenedor_odoo}" y costos "${f.contenedor_costo}", y una de las dos no trae número de embarque. Puede ser el booking y el contenedor del mismo embarque, o dos cosas distintas — no hay con qué saberlo.`}
                >
                  · sin cotejar
                </span>
              )}
            </>
          ) : "sin contenedor"}
        </div>
      </td>

      <td className="px-3 py-2.5 text-right">
        <div className="tabular-nums text-slate-700">{num(f.cajas)}</div>
        {!!f.cajas_por_llegar && (
          <div className="text-[10px] text-amber-700" title="Cajas de lo que sigue en recepción abierta">
            +{num(f.cajas_por_llegar)} por llegar
          </div>
        )}
      </td>

      <td className="px-3 py-2.5 text-right">
        <span className={`font-bold tabular-nums ${
          piezas === null ? "text-slate-300" : piezas > 0 ? "text-emerald-700" : "text-rose-600"}`}>
          {num(piezas)}
        </span>
        {!!f.recepcion_piezas && (
          <div
            className="text-[10px] font-semibold text-amber-700"
            title={`${f.recepcion_docs > 1 ? `${f.recepcion_docs} recepciones ABIERTAS` : `Recepción ${f.recepcion_ref ?? ""} ABIERTA`}; la más vieja desde ${f.recepcion_desde.slice(0, 10)} — ninguna programada a futuro`}
          >
            +{num(f.recepcion_piezas)} en{" "}
            {f.recepcion_docs > 1 ? `${f.recepcion_docs} recepciones` : "recepción"}
            {" · "}{f.recepcion_dias}d
          </div>
        )}
        {!!f.no_vendible && (
          <div className="text-[10px] text-rose-600" title="En cuarentena o scrap: están en bodega y no se pueden vender">
            {num(f.no_vendible)} no vendibles
          </div>
        )}
      </td>

      <td className="px-3 py-2.5 text-right tabular-nums text-slate-500">{num(f.reservado)}</td>

      {/* UBICACIÓN: el rack arriba, la bodega abajo. Dato que solo tiene Odoo. */}
      <td className="px-3 py-2.5">
        {f.rack ? (
          <>
            <div className="flex items-center gap-1 font-mono text-xs text-slate-700">
              <MapPin className="h-3 w-3 shrink-0 text-slate-300" />
              {f.rack}
            </div>
            <div className="text-[10px] text-slate-400">
              {f.bodega}
              {f.n_ubicaciones > 1 && ` +${f.n_ubicaciones - 1} más`}
            </div>
          </>
        ) : (
          <span className="text-xs text-slate-300">sin ubicación</span>
        )}
      </td>

      <td className="px-3 py-2.5">
        <span title={f.cuadre.detalle}
              className={`inline-block rounded px-2 py-0.5 text-[11px] font-bold ring-1 ${ESTILO_CUADRE[f.cuadre.estado]}`}>
          {f.cuadre.etiqueta}
        </span>
      </td>

      <td className="px-3 py-2.5">
        <div className="flex items-center gap-1">
          {ETAPAS.map(({ clave, titulo, icono: Icono }) => {
            const e = f.etapas[clave];
            return (
              <span key={clave}
                    title={`${titulo}: ${e.etiqueta}${e.detalle ? ` — ${e.detalle}` : ""}`}
                    className={`inline-flex h-6 w-6 items-center justify-center rounded-md border ${ESTILO_ETAPA[e.estado]}`}>
                <Icono className="h-3.5 w-3.5" />
              </span>
            );
          })}
        </div>
      </td>

      {/* Dos botones, como el diseño: «Historial» abre la FICHA (con el resumen
          de movimientos dentro) y el ⇅ salta directo a la pantalla completa de
          trazabilidad, sin pasar por la ficha. */}
      <td className="px-3 py-2.5 text-right" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-end gap-1">
          <button
            type="button" onClick={() => onAbrir(f)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-600 hover:border-indigo-200 hover:text-indigo-600"
          >
            <History className="h-3.5 w-3.5" /> Historial
          </button>
          <button
            type="button" onClick={() => onTraza(f)}
            title="Ver la trazabilidad completa de este SKU"
            className="rounded-lg border border-slate-200 bg-white p-1.5 text-slate-500 hover:border-indigo-200 hover:text-indigo-600"
          >
            <ArrowUpDown className="h-3.5 w-3.5" />
          </button>
        </div>
      </td>
    </tr>
  );
}

function Paginacion({
  pagina, total, skus, onPagina,
}: { pagina: number; total: number; skus: number; onPagina: (p: number) => void }) {
  if (total <= 1) {
    return <p className="mt-3 text-xs text-slate-400">{num(skus)} SKUs</p>;
  }
  const paginas = Array.from({ length: total }, (_, i) => i + 1)
    .filter((p) => p === 1 || p === total || Math.abs(p - pagina) <= 1);
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <span className="text-xs text-slate-400">
        Página <b className="text-slate-600">{pagina}</b> de {total} · {num(skus)} SKUs
      </span>
      <div className="ml-auto flex items-center gap-1">
        {paginas.map((p, i) => (
          <span key={p} className="flex items-center gap-1">
            {i > 0 && p - paginas[i - 1] > 1 && <span className="px-1 text-slate-300">…</span>}
            <button
              type="button" onClick={() => onPagina(p)}
              className={`h-8 min-w-8 rounded-lg px-2 text-xs font-bold ${
                p === pagina ? "bg-indigo-600 text-white"
                  : "border border-slate-200 bg-white text-slate-500 hover:bg-slate-50"}`}
            >
              {p}
            </button>
          </span>
        ))}
      </div>
    </div>
  );
}

/* ──────────── el cajón: ficha del SKU (1d) + trazabilidad (1f) ──────────── */

function Cajon({
  fila, onCerrar, onTraza,
}: {
  fila: FilaInventario; onCerrar: () => void; onTraza: () => void;
}) {
  const [movs, setMovs] = useState<MovimientosResp | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCerrar();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCerrar]);

  // Aquí solo se pide el RESUMEN: los últimos movimientos reales. El detalle
  // completo, con filtros y ventana, vive en la pantalla de trazabilidad.
  useEffect(() => {
    const ctrl = new AbortController();
    setCargando(true);
    setError(null);
    movimientosInventario(fila.sku, "reales", 6, null, ctrl.signal)
      .then(setMovs)
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        setError(mensajeDeError(e, "No se pudo leer el flujo de movimientos."));
      })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [fila.sku]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm" onClick={onCerrar} />
      <aside className="relative flex h-full w-full max-w-2xl animate-slide-in flex-col bg-slate-50 shadow-2xl">
        <header className="flex items-start justify-between gap-3 border-b border-slate-200 bg-white px-5 py-4">
          <div className="flex min-w-0 gap-3">
            {fila.imagen ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={fila.imagen} alt=""
                   className="h-14 w-14 shrink-0 rounded-xl border border-slate-200 object-cover" />
            ) : (
              <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50">
                <Camera className="h-5 w-5 text-slate-300" />
              </div>
            )}
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] font-bold text-slate-700">
                  {fila.sku}
                </span>
                {fila.es_padre && (
                  <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-bold text-indigo-600">
                    Padre · {fila.n_hijas} variantes
                  </span>
                )}
                {fila.n_variantes_odoo > 0 && (
                  <span
                    title="SKUs relacionados en Odoo (misma plantilla o mismo código base)"
                    className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold text-slate-500"
                  >
                    {fila.n_variantes_odoo + 1} SKUs en Odoo
                  </span>
                )}
                {fila.etapas.validado.estado === "listo" && (
                  <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-bold text-emerald-700">
                    Costo validado
                  </span>
                )}
              </div>
              <h2 className="mt-1 truncate text-base font-bold text-slate-900">
                {fila.nombre || "—"}
              </h2>
              {fila.canales.length > 0 && (
                <p className="truncate text-xs text-slate-400">
                  publicado en {[...new Set(fila.canales.map((c) => c.canal))].join(", ")}
                </p>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {/* El botón del diseño 1d: salta a la trazabilidad completa. */}
            <button
              type="button" onClick={onTraza}
              className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-semibold text-white hover:bg-indigo-700"
            >
              <ArrowUpDown className="h-4 w-4" /> Movimiento
            </button>
            <button type="button" onClick={onCerrar}
                    className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
              <X className="h-5 w-5" />
            </button>
          </div>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5">
          <Jerarquia fila={fila} />
          <DondeEsta fila={fila} />
          <Etapas fila={fila} />

          <FlujoResumen movs={movs} cargando={cargando} error={error}
                        onTraza={onTraza} />
        </div>
      </aside>
    </div>
  );
}

function Jerarquia({ fila }: { fila: FilaInventario }) {
  const paso = (t: string, v: string, sub?: string, destacado?: boolean) => (
    <div className={`flex-1 rounded-xl border p-3 ${
      destacado ? "border-indigo-200 bg-indigo-50" : "border-slate-200 bg-white"}`}>
      <div className="text-[10px] font-bold uppercase tracking-[0.06em] text-slate-400">{t}</div>
      <div className={`mt-1 text-xl font-extrabold tabular-nums ${
        destacado ? "text-indigo-700" : "text-slate-900"}`}>{v}</div>
      {sub && <div className="mt-0.5 text-[11px] leading-tight text-slate-400">{sub}</div>}
    </div>
  );
  const signo = (s: string) => (
    <span className="self-center px-1 text-sm font-bold text-slate-300">{s}</span>
  );
  return (
    <section>
      <h3 className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
        Jerarquía de empaque
      </h3>
      <div className="mt-2 flex gap-1">
        {paso("Contenedor", fila.contenedor || "—",
          fila.contenedor_fuente === "odoo" ? "según Odoo"
            : fila.contenedor_fuente === "costos_validados" ? "según costos_validados"
              : undefined)}
        {signo("=")}
        {paso("Cajas", num(fila.cajas), "en bodega")}
        {signo("×")}
        {paso("Piezas / caja", fila.piezas_por_caja === null ? "—" : String(fila.piezas_por_caja),
          "caja master de Odoo")}
        {signo("=")}
        {paso("Piezas", num(fila.stock_fisico), "físico en Odoo", true)}
      </div>
      {!!fila.cajas_por_llegar && (
        <p className="mt-1.5 text-[11px] text-slate-400">
          Por llegar: {num(fila.recepcion_piezas)} piezas ≈ {num(fila.cajas_por_llegar)} cajas.
        </p>
      )}
      {fila.embarque && (
        <p className="mt-1 text-[11px] text-slate-400">Embarque {fila.embarque}.</p>
      )}
    </section>
  );
}

function DondeEsta({ fila }: { fila: FilaInventario }) {
  const linea = (izq: React.ReactNode, der: string, tono = "text-slate-900") => (
    <div className="flex items-baseline justify-between gap-3 py-1.5 text-sm">
      <span className="min-w-0 truncate text-slate-500">{izq}</span>
      <span className={`shrink-0 font-bold tabular-nums ${tono}`}>{der}</span>
    </div>
  );
  return (
    <section className="grid gap-3 sm:grid-cols-2">
      <div className="rounded-xl border border-slate-200 bg-white p-3">
        <h3 className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
          Dónde está
        </h3>
        <div className="mt-1 divide-y divide-slate-100">
          {fila.ubicaciones.length === 0 && (
            <p className="py-2 text-xs text-slate-400">Sin existencias en ninguna ubicación.</p>
          )}
          {fila.ubicaciones.map((u) => (
            <div key={u.ubicacion}>
              {linea(
                <>
                  {u.bodega} · <span className="font-mono text-xs text-slate-700">{u.rack}</span>
                  {!u.vendible && (
                    <span className="ml-1 rounded bg-rose-50 px-1 py-0.5 text-[10px] font-bold text-rose-700">
                      no vendible
                    </span>
                  )}
                </>,
                `${num(u.piezas)} pzs`,
                u.vendible ? "text-slate-900" : "text-rose-600",
              )}
            </div>
          ))}
          {!!fila.stock_full && linea("Mercado Libre FULL", `${num(fila.stock_full)} pzs`, "text-amber-700")}
          {!!fila.stock_fba && linea("Amazon FBA", `${num(fila.stock_fba)} pzs`, "text-sky-700")}
          {!!fila.reservado && linea("Reservado", `${num(fila.reservado)} pzs`, "text-slate-500")}
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-3">
        <div className="flex items-center justify-between">
          <h3 className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
            Cuadre con los canales
          </h3>
          <span className={`rounded px-2 py-0.5 text-[11px] font-bold ring-1 ${ESTILO_CUADRE[fila.cuadre.estado]}`}>
            {fila.cuadre.etiqueta}
          </span>
        </div>
        <div className="mt-1 divide-y divide-slate-100">
          {linea("Disponible en Odoo", num(fila.stock_odoo))}
          {linea("Físico en racks", num(fila.stock_fisico))}
          {linea("WooCommerce", num(fila.stock_woo),
            fila.descuadre ? "text-rose-600" : "text-slate-900")}
          {fila.canales.map((c, i) => (
            <div key={`${c.canal}-${c.listing_id ?? i}`}>
              {linea(
                <>{c.canal}{c.fulfillment ? " · FULL" : ""}</>,
                c.status ?? "—",
                "text-slate-400",
              )}
            </div>
          ))}
        </div>
        {!!fila.recepcion_piezas && (
          <p className="mt-2 rounded-lg bg-amber-50 p-2 text-[11px] leading-snug text-amber-800 ring-1 ring-amber-200">
            <Ship className="mr-1 inline h-3 w-3" />
            {num(fila.recepcion_piezas)} piezas en{" "}
            {fila.recepcion_docs > 1
              ? `${fila.recepcion_docs} recepciones que siguen ABIERTAS`
              : "una recepción que sigue ABIERTA"}
            {fila.recepcion_dias !== null && `; la más vieja desde hace ${fila.recepcion_dias} días`}
            {fila.recepcion_ref && ` (${fila.recepcion_ref})`}. Ninguna está
            programada a futuro: son recepciones sin validar, no mercancía en camino.
          </p>
        )}
      </div>
    </section>
  );
}

function Etapas({ fila }: { fila: FilaInventario }) {
  return (
    <section>
      <h3 className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
        Estatus de proceso
      </h3>
      <div className="mt-2 space-y-1.5">
        {ETAPAS.map(({ clave, titulo, icono: Icono }) => {
          const e = fila.etapas[clave];
          return (
            <div key={clave}
                 className={`flex items-start gap-3 rounded-xl border p-2.5 ${ESTILO_ETAPA[e.estado]}`}>
              <Icono className="mt-0.5 h-4 w-4 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-sm font-bold">{titulo}</span>
                  <span className="text-xs font-semibold">{e.etiqueta}</span>
                </div>
                {e.detalle && <div className="text-[11px] opacity-80">{e.detalle}</div>}
                {clave === "variantes" && fila.variantes_odoo.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {fila.variantes_odoo.map((v) => (
                      <span
                        key={v.sku}
                        title={`${v.nombre}${v.relacion === "plantilla"
                          ? " · misma plantilla en Odoo"
                          : " · solo comparte código base, NO son variantes en Odoo"}`}
                        className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-bold ${
                          v.relacion === "plantilla"
                            ? "bg-white/70 ring-1 ring-current"
                            : "bg-white/40 opacity-70"}`}
                      >
                        {v.sku}
                        {v.relacion === "codigo" && " ·código"}
                      </span>
                    ))}
                  </div>
                )}
                {e.ultimo_paso && (
                  <div className="mt-0.5 text-[11px] opacity-70">
                    último paso: {e.ultimo_paso}
                    {e.ultimo_actor ? ` · ${e.ultimo_actor}` : ""}
                    {e.ultimo_at ? ` · ${e.ultimo_at.slice(0, 10)}` : ""}
                  </div>
                )}
                {/* De dónde salió el dato: para poder discutirlo, no solo verlo. */}
                <div className="mt-0.5 font-mono text-[10px] opacity-50">{e.fuente}</div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

/* ─────────── resumen de movimientos DENTRO de la ficha (1d) ─────────── */

/**
 * «Flujo de movimientos del SKU»: los últimos seis renglones y una salida a la
 * pantalla completa. Es un RESUMEN a propósito — el detalle con filtros,
 * ventana y CSV vive en `Trazabilidad`, detrás del botón «Movimiento».
 */
function FlujoResumen({
  movs, cargando, error, onTraza,
}: {
  movs: MovimientosResp | null;
  cargando: boolean;
  error: string | null;
  onTraza: () => void;
}) {
  return (
    <section>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
          <History className="h-3.5 w-3.5" />
          Flujo de movimientos del SKU
        </h3>
        <button
          type="button" onClick={onTraza}
          className="rounded-lg px-2 py-1 text-xs font-semibold text-indigo-600 hover:bg-indigo-50"
        >
          Ver historial completo →
        </button>
      </div>

      {movs && movs.cuadra === false && (
        <div className="mt-2 flex items-start gap-2 rounded-lg bg-amber-50 p-2.5 text-xs text-amber-800 ring-1 ring-amber-200">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            El libro suma {num(movs.saldo_libro)} y Odoo publica {num(movs.saldo_odoo)}.
            La diferencia es real y hay que revisarla con Inventarios — no es un
            error de esta pantalla.
          </span>
        </div>
      )}

      {error && (
        <div className="mt-2 rounded-lg bg-rose-50 p-2.5 text-xs text-rose-700 ring-1 ring-rose-200">
          {error}
        </div>
      )}

      {cargando ? (
        <div className="py-8 text-center text-slate-400">
          <Loader2 className="mx-auto h-5 w-5 animate-spin" />
          <p className="mt-2 text-xs">Leyendo el libro de Odoo…</p>
        </div>
      ) : !movs?.movimientos.length ? (
        <p className="mt-2 rounded-xl border border-slate-200 bg-white py-8 text-center text-xs text-slate-400">
          Sin movimientos registrados en Odoo para este SKU.
        </p>
      ) : (
        <>
          <div className="mt-2 divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white">
            {movs.movimientos.map((m, i) => (
              <RenglonResumen key={`${m.documento}-${m.fecha}-${i}`} m={m} />
            ))}
          </div>
          <p className="mt-1.5 text-[11px] text-slate-400">
            {movs.movimientos.length} de {num(movs.total_historico)} movimientos ·
            saldo {num(movs.saldo_libro)}
            {movs.cuadra && " · cuadra con Odoo"}
          </p>
        </>
      )}
    </section>
  );
}

function RenglonResumen({ m }: { m: Movimiento }) {
  const Icono = ICONO_CAUSA[m.causa] ?? Package;
  return (
    <div className="flex items-center gap-3 px-3 py-2">
      <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ring-1 ${
        COLOR_CAUSA[m.causa] ?? "bg-slate-100 text-slate-500 ring-slate-200"}`}>
        <Icono className="h-3.5 w-3.5" />
      </span>
      <span className="w-24 shrink-0 text-[11px] tabular-nums text-slate-400">
        {fechaCorta(m.fecha)}
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-semibold text-slate-700">
          {ETIQUETA_CAUSA[m.causa] ?? m.causa}
          {m.contraparte ? ` · ${m.contraparte}` : ""}
        </div>
        <div className="truncate font-mono text-[10px] text-slate-400">
          {m.documento}{m.referencia ? ` · ${m.referencia}` : ""}
        </div>
      </div>
      <span className={`w-16 shrink-0 text-right text-xs font-bold tabular-nums ${
        m.delta > 0 ? "text-emerald-700" : m.delta < 0 ? "text-rose-700" : "text-slate-300"}`}>
        {m.delta > 0 ? "+" : ""}{m.delta === 0 ? "—" : num(m.delta)}
      </span>
      <span className="w-20 shrink-0 text-right text-[11px] tabular-nums text-slate-400">
        saldo {num(m.saldo)}
      </span>
      <span className="w-24 shrink-0 truncate text-right text-[11px] text-slate-400">
        {m.quien || "—"}
      </span>
    </div>
  );
}

/** `2026-08-26T17:44:51` → `26 ago · 17:44`, como el diseño. */
function fechaCorta(iso: string): string {
  const d = new Date(iso.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  const mes = ["ene", "feb", "mar", "abr", "may", "jun",
               "jul", "ago", "sep", "oct", "nov", "dic"][d.getMonth()];
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${String(d.getDate()).padStart(2, "0")} ${mes} · ${hh}:${mm}`;
}

/* ───────────── TRAZABILIDAD · la pantalla completa (diseño 1f) ───────────── */

/**
 * Entradas, ventas, envíos a FULL/FBA, devoluciones, ajustes, traspasos y
 * mermas de un SKU, con saldo corriente y exportable a CSV.
 *
 * Se abre desde el ⇅ de la tabla o desde «Movimiento» en la ficha. Es un modal
 * a pantalla completa y no una ruta propia porque siempre se llega desde una
 * fila: una URL `/inventario/<sku>/movimientos` obligaría a recargar toda la
 * tabla al volver, que son otros 4 segundos contra tres bases.
 *
 * DOS CHIPS DEL DISEÑO QUE NO ESTÁN, Y ESTÁ BIEN QUE NO ESTÉN:
 *   · «Reservas» — Odoo no registra las reservas como movimientos: son un campo
 *     del quant. El chip existiría siempre vacío, que es peor que no existir.
 *     Lo reservado sí se muestra, en la ficha.
 *   · «Incluir histórico de Odoo» — TODO el histórico es de Odoo; el panel no
 *     escribe un solo movimiento. En su lugar va el toggle que sí importa:
 *     mostrar u ocultar los pasos internos PICK/PACK, que son la mayoría de los
 *     renglones y no mueven saldo.
 */
function Trazabilidad({
  fila, onCerrar,
}: { fila: FilaInventario; onCerrar: () => void }) {
  const [movs, setMovs] = useState<MovimientosResp | null>(null);
  const [causa, setCausa] = useState("reales");
  const [dias, setDias] = useState(90);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCerrar();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCerrar]);

  useEffect(() => {
    const ctrl = new AbortController();
    setCargando(true);
    setError(null);
    movimientosInventario(fila.sku, causa, 1000, dias || null, ctrl.signal)
      .then(setMovs)
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        setError(mensajeDeError(e, "No se pudo leer la trazabilidad."));
      })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [fila.sku, causa, dias]);

  // El CSV se arma en el navegador con lo que ya está cargado: no hace falta un
  // endpoint nuevo (ni su línea de RBAC, ni volver a pegarle a Odoo).
  const csv = useCallback(() => {
    if (!movs?.movimientos.length) return;
    const cab = ["fecha", "tipo", "concepto", "documento", "referencia",
                 "contraparte", "almacen", "cantidad", "saldo", "quien"];
    const esc = (v: unknown) => JSON.stringify(String(v ?? ""));
    const cuerpo = movs.movimientos.map((m) => [
      m.fecha, m.causa, ETIQUETA_CAUSA[m.causa] ?? m.causa, m.documento,
      m.referencia, m.contraparte, m.almacen, m.delta, m.saldo ?? "", m.quien,
    ].map(esc).join(","));
    // El BOM va delante o Excel abre los acentos como mojibake.
    const texto = "﻿" + [cab.map(esc).join(","), ...cuerpo].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([texto], { type: "text/csv;charset=utf-8" }));
    a.download = `movimientos_${fila.sku}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 30_000);
  }, [movs, fila.sku]);

  const chip = (activo: boolean) =>
    `rounded-full px-3 py-1.5 text-xs font-bold transition ${
      activo ? "bg-indigo-600 text-white"
             : "bg-white text-slate-500 ring-1 ring-slate-200 hover:bg-slate-50"}`;

  return (
    <div className="fixed inset-0 z-[60] overflow-y-auto bg-slate-900/50 p-4 backdrop-blur-sm sm:p-8"
         onClick={onCerrar}>
      <div className="mx-auto max-w-5xl rounded-2xl bg-white shadow-2xl"
           onClick={(e) => e.stopPropagation()}>
        <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 px-6 py-5">
          <div className="min-w-0">
            <p className="text-[11px] font-bold uppercase tracking-[0.08em] text-slate-400">
              Trazabilidad
            </p>
            <h2 className="mt-0.5 flex flex-wrap items-baseline gap-2 text-2xl font-extrabold tracking-tight text-slate-900">
              Movimientos
              <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-sm font-bold text-slate-600">
                {fila.sku}
              </span>
            </h2>
            <p className="mt-0.5 truncate text-sm text-slate-500">{fila.nombre}</p>
          </div>
          <div className="flex items-center gap-2">
            <select value={dias} onChange={(e) => setDias(Number(e.target.value))}
                    className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-600 outline-none">
              {VENTANAS.map((v) => <option key={v.v} value={v.v}>{v.t}</option>)}
            </select>
            <button
              type="button" onClick={csv} disabled={!movs?.movimientos.length}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-40"
            >
              <Download className="h-4 w-4" /> CSV
            </button>
            <button type="button" onClick={onCerrar}
                    className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
              <X className="h-5 w-5" />
            </button>
          </div>
        </header>

        <div className="flex flex-wrap items-center gap-1.5 border-b border-slate-200 px-6 py-3">
          {CAUSAS.filter((c) => c.v !== "todo").map((c) => (
            <button key={c.v} type="button" onClick={() => setCausa(c.v)}
                    className={chip(causa === c.v)}>
              {c.t}
              {movs && c.v !== "reales" && (
                <span className="ml-1 opacity-60">{movs.por_causa[c.v] ?? 0}</span>
              )}
            </button>
          ))}
          <button
            type="button" onClick={() => setCausa(causa === "todo" ? "reales" : "todo")}
            title="Los pasos PICK/PACK de Odoo: no mueven saldo y son la mayoría de los renglones"
            className={`ml-auto flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold ${
              causa === "todo" ? "bg-slate-800 text-white"
                               : "bg-white text-slate-500 ring-1 ring-slate-200 hover:bg-slate-50"}`}
          >
            <Package className="h-3.5 w-3.5" />
            Incluir pasos internos
          </button>
        </div>

        {movs && movs.cuadra === false && (
          <div className="mx-6 mt-4 flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-800 ring-1 ring-amber-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              El libro suma {num(movs.saldo_libro)} y Odoo publica {num(movs.saldo_odoo)}.
              La diferencia es real y hay que revisarla con Inventarios — no es un
              error de esta pantalla.
            </span>
          </div>
        )}

        {error && (
          <div className="mx-6 mt-4 rounded-lg bg-rose-50 p-3 text-sm text-rose-700 ring-1 ring-rose-200">
            {error}
          </div>
        )}

        {cargando ? (
          <div className="py-20 text-center text-slate-400">
            <Loader2 className="mx-auto h-6 w-6 animate-spin" />
            <p className="mt-3 text-sm">Leyendo el libro de Odoo…</p>
          </div>
        ) : !movs?.movimientos.length ? (
          <p className="py-20 text-center text-sm text-slate-400">
            Sin movimientos de este tipo en la ventana elegida.
          </p>
        ) : (
          <div className="divide-y divide-slate-100">
            {movs.movimientos.map((m, i) => (
              <RenglonTraza key={`${m.documento}-${m.fecha}-${i}`} m={m} />
            ))}
          </div>
        )}

        <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 px-6 py-4 text-xs text-slate-400">
          <span>
            Mostrando {movs?.movimientos.length ?? 0} de{" "}
            {num(movs?.total_historico)} movimientos
            {movs ? ` · saldo ${num(movs.saldo_libro)}` : ""}
          </span>
          <span className="flex items-center gap-1.5">
            <Lock className="h-3.5 w-3.5" />
            Registro de Odoo, inmutable: un error se corrige con un ajuste, no
            borrando el renglón.
          </span>
        </footer>
      </div>
    </div>
  );
}

function RenglonTraza({ m }: { m: Movimiento }) {
  const Icono = ICONO_CAUSA[m.causa] ?? Package;
  return (
    <div className={`flex flex-wrap items-center gap-3 px-6 py-3 hover:bg-slate-50/70 ${
      m.interno ? "opacity-50" : ""}`}>
      <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ring-1 ${
        COLOR_CAUSA[m.causa] ?? "bg-slate-100 text-slate-500 ring-slate-200"}`}>
        <Icono className="h-4 w-4" />
      </span>

      <span className="w-28 shrink-0 text-xs tabular-nums text-slate-400">
        {fechaCorta(m.fecha)}
      </span>

      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-slate-800">
          {ETIQUETA_CAUSA[m.causa] ?? m.causa}
          {m.contraparte ? ` · ${m.contraparte}` : ""}
        </div>
        <div className="truncate text-xs text-slate-400">
          <span className="font-mono">{m.documento || "—"}</span>
          {m.referencia && <span className="font-mono"> · {m.referencia}</span>}
          {m.almacen && ` · ${m.almacen}`}
          {m.pedido !== null && (
            <span className="text-amber-700"> · pedidas {num(m.pedido)}</span>
          )}
        </div>
      </div>

      <span className={`w-24 shrink-0 text-right text-sm font-bold tabular-nums ${
        m.delta > 0 ? "text-emerald-700" : m.delta < 0 ? "text-rose-700" : "text-slate-300"}`}>
        {m.delta > 0 ? "+" : ""}{m.delta === 0 ? "sin efecto" : num(m.delta)}
      </span>

      <span className="w-20 shrink-0 text-right text-sm tabular-nums text-slate-500">
        {num(m.saldo)}
      </span>

      <span className="w-32 shrink-0 truncate text-right text-xs text-slate-400">
        {m.quien || "—"}
      </span>
    </div>
  );
}
