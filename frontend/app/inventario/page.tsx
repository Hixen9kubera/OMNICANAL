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
  ChevronRight, ClipboardList, Clock, Container, Database, Download,
  FileClock, History, Layers, Loader2, Lock, MapPin, Package, PackagePlus,
  PackageX, ScrollText,
  PackageSearch, RefreshCw, RotateCcw, ShieldAlert, Ship, ShoppingCart, Truck,
  X,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import { listarInventario, mensajeDeError, movimientosInventario } from "@/lib/api";
import type {
  ClaveEtapa, ClavePunto, Cuadre, EstadoEtapa, EstadoPunto, FilaInventario,
  InventarioResp, Movimiento, MovimientosResp, OrdenCompra,
  RecepcionPendiente,
} from "@/lib/types";

/**
 * LOS TRES ALMACENES FÍSICOS, y DROP OFF se distingue a la vista.
 *
 * Brandon, 9-sep: «debe mostrarse los productos que pertenezcan a texco 1,
 * texco 2 o DROP OFF; este último debe mostrarse de otro color». No es
 * decoración: DROP OFF es el almacén del que salen los envíos a los
 * marketplaces chinos, o sea que una pieza ahí está comprometida a un flujo
 * distinto del de TEXCO. Verlo de un golpe cambia la decisión.
 *
 * Los nombres salen del `warehouse_id` de Odoo TAL CUAL — son TEXCO (id 135),
 * TEXCO II (150) y DROP OFF (142)—, así que aquí no se normaliza nada: si
 * alguien renombra un almacén en Odoo, aparece el nombre nuevo sin estilo
 * propio en vez de desaparecer. Ojo con eso: `odoo_ventas.py:406` documenta que
 * las fotos se llavean por NOMBRE de almacén y renombrarlo ya rompió algo.
 *
 * «sin almacén» es el cuarto cubo y NO es una bodega: son SCRAP y CUARENTENA,
 * las dos ubicaciones internas sin `warehouse_id` que Odoo excluye de
 * `qty_available`. Se pinta en rojo tenue porque esa mercancía no se vende.
 */
const ESTILO_BODEGA: Record<string, string> = {
  "DROP OFF": "bg-violet-100 text-violet-700 ring-violet-200",
  "TEXCO": "bg-sky-100 text-sky-700 ring-sky-200",
  "TEXCO II": "bg-teal-100 text-teal-700 ring-teal-200",
  "sin almacén": "bg-rose-50 text-rose-600 ring-rose-200",
};
const ESTILO_BODEGA_OTRA = "bg-slate-100 text-slate-600 ring-slate-200";

function Almacen({ nombre, titulo }: { nombre: string; titulo?: string }) {
  return (
    <span
      title={titulo ?? (nombre === "DROP OFF"
        ? "DROP OFF: el almacén del que salen los envíos a marketplaces chinos"
        : `Almacén ${nombre} según Odoo`)}
      className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-bold ring-1 ${
        ESTILO_BODEGA[nombre] ?? ESTILO_BODEGA_OTRA}`}
    >
      {nombre}
    </span>
  );
}

/** El estado de uno de los cuatro requisitos, por su clave. */
function punto(f: FilaInventario, clave: ClavePunto): EstadoPunto | null {
  return f.validacion_bodega.puntos.find((p) => p.clave === clave)?.estado ?? null;
}

/** Los cuatro requisitos de VALIDADO BODEGA, en el orden que los dio Brandon. */
const PUNTOS: { clave: ClavePunto; icono: typeof Camera }[] = [
  { clave: "ubicacion", icono: MapPin },
  { clave: "stock", icono: Boxes },
  { clave: "foto", icono: Camera },
  { clave: "specs", icono: ClipboardList },
];

const ESTILO_PUNTO: Record<EstadoPunto, string> = {
  listo: "border-emerald-200 bg-emerald-50 text-emerald-700",
  falta: "border-rose-200 bg-rose-50 text-rose-700",
  // Ámbar y no rojo: el canal para recibir el dato todavía no existe, así que
  // no es culpa del producto.
  espera: "border-amber-200 bg-amber-50 text-amber-700",
  na: "border-slate-200 bg-white text-slate-300",
};

/** Los dos indicadores que NO son de bodega. */
const ETAPAS: { clave: ClaveEtapa; titulo: string; icono: typeof Camera }[] = [
  { clave: "validado", titulo: "Costo validado", icono: CheckCircle2 },
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
  { v: 0, t: "Todo el histórico" },
  { v: 365, t: "Último año" },
  { v: 90, t: "Últimos 90 días" },
  { v: 30, t: "Últimos 30 días" },
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

/** Como `num`, pero conserva decimales cuando redondear mentiría.
 *  Las cajas se derivan (piezas ÷ factor) y salen fraccionarias: `MUE-0135-NEG`
 *  tiene 1 pieza con factor 3, o sea 0.33 cajas. Redondeado se leía «0», que
 *  es justo lo contrario de lo que pasa — hay mercancía. */
const numCajas = (v: number | null | undefined) => {
  if (v === null || v === undefined) return "—";
  if (Number.isInteger(v)) return v.toLocaleString("es-MX");
  return v < 10 ? v.toFixed(2).replace(/0$/, "") : Math.round(v).toLocaleString("es-MX");
};

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
        activo_sin_stock: (f) => f.cuadre.etiqueta === "Activo sin stock",
        descuadre: (f) => !!f.descuadre,
        recepcion_vencida: (f) => (f.recepcion_dias ?? 0) > 30,
        sin_ubicacion: (f) => !f.n_ubicaciones,
        sin_fotos: (f) => punto(f, "foto") === "falta",
        sin_costo: (f) => f.comercial.validado.estado === "pendiente",
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
                {resumen.completos} de {resumen.skus} validados por bodega
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
      // «En recepción» se leía como un lugar. Es un ESTADO: piezas que Odoo
      // tiene en documentos de recepción que nadie validó.
      t: "Sin recibir", v: resumen.en_recepcion, i: Ship, tono: "aviso",
      p: resumen.alertas.recepcion_vencida
        ? `${resumen.alertas.recepcion_vencida} SKUs con recepción vencida`
        : "en recepciones abiertas de Odoo",
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
    { k: "descuadre", t: "Descuadre Woo ↔ físico", n: resumen.alertas.descuadre, tono: "peligro" },
    { k: "sin_alta", t: "Sin alta en Woo", n: resumen.alertas.sin_alta, tono: "peligro" },
    { k: "odoo_duplicado", t: "Duplicado en Odoo", n: resumen.alertas.odoo_duplicado, tono: "peligro" },
    { k: "sin_ubicacion", t: "Sin ubicación en bodega", n: resumen.alertas.sin_ubicacion, tono: "peligro" },
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
      {/* Solo se ofrecen las bodegas que APARECEN en las filas que se estan
          viendo, no las tres siempre: ofrecer un almacen vacio seria prometer
          un filtro que devuelve cero. */}
      <select value={bodega} onChange={(e) => setBodega(e.target.value)}
              className={campo}
              title="Los tres almacenes fisicos son TEXCO, TEXCO II y DROP OFF. Aqui solo salen los que aparecen en las filas que estas viendo.">
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
            <th className="px-3 py-3 text-right font-bold"
                title="BOD = las que contó almacén (mandan, canal no construido). PL = las del packing list del proveedor. «En piso» = derivada de las piezas libres de Odoo.">
              Cajas
            </th>
            <th className="px-3 py-3 text-right font-bold"
                title="Free to use: lo vendible. El on hand se muestra debajo, y solo cuando difiere.">
              Piezas
            </th>
            <th className="px-3 py-3 text-right font-bold">Reserv.</th>
            <th className="px-3 py-3 text-left font-bold">Ubicación</th>
            <th className="px-3 py-3 text-left font-bold">Woo ↔ físico</th>
            <th className="px-3 py-3 text-left font-bold"
                title="Ubicación · Stock · Foto · Specs. Los cuatro requisitos para dar un producto por validado en bodega.">
              Validado bodega
            </th>
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
          {f.es_referencia && (
            <span title="SKU de ejemplo con movimiento real: sirve para ver cómo funciona la trazabilidad"
                  className="rounded bg-violet-50 px-1.5 py-0.5 text-[10px] font-bold text-violet-600">
              ejemplo
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

      {/* Tres cajas, tres preguntas: la que MANDA es la de bodega y no existe.
          Se pinta igual —en ámbar y vacía— porque un hueco rotulado se puede
          exigir y una columna ausente no. */}
      <td className="px-3 py-2.5 text-right">
        <div className="flex items-baseline justify-end gap-1.5"
             title="Cajas que contó ALMACÉN al recibir. Mandan sobre las del packing list. Hoy no existe el canal para recibir ese dato.">
          <span className="text-[9px] font-bold uppercase tracking-wide text-amber-600">bod</span>
          <span className="tabular-nums font-bold text-amber-600">—</span>
        </div>
        <div className="flex items-baseline justify-end gap-1.5"
             title={!f.cotejo_cajas || f.cotejo_cajas.packing_list === null
               ? "No hay cajas del packing list para este SKU: ni renglón registrado ni cifra en costos_validados."
               : f.cotejo_cajas.pl_fuente === "renglon"
                 ? `${numCajas(f.cotejo_cajas.packing_list)} cajas leídas del RENGLÓN ${f.cotejo_cajas.pl_renglones?.join(", ")} de ${f.cotejo_cajas.pl_archivo}`
                   + (f.cotejo_cajas.pl_compartida
                       ? ` — cartón COMPARTIDO entre ${f.cotejo_cajas.pl_renglones_carton} renglones`
                       : "")
                   + (f.cotejo_cajas.pl_congelado !== null
                       ? `. Ojo: costos_validados dice ${f.cotejo_cajas.pl_congelado} — discrepan.`
                       : "")
                 : `${numCajas(f.cotejo_cajas.packing_list)} cajas según costos_validados (cifra CONGELADA de mayo/junio; este SKU no tiene renglón registrado).`}>
          <span className="text-[9px] font-bold uppercase tracking-wide text-slate-400">pl</span>
          <span className={`tabular-nums ${
            f.cotejo_cajas?.packing_list === null ? "text-slate-300" : "font-bold text-slate-700"}`}>
            {numCajas(f.cotejo_cajas?.packing_list)}
          </span>
        </div>
        {f.cajas !== null && (
          <div className="text-[10px] text-slate-400"
               title="Cajas que llenarían las piezas LIBRES de hoy en Odoo. Es una derivación (libres ÷ piezas por caja), no un conteo.">
            {numCajas(f.cajas)} en piso
          </div>
        )}
        {/* Las CAJAS POR RECIBIR se retiran de la tabla (Brandon, 9-sep): un
            SKU puede tener varias órdenes de compra abiertas, y una sola cifra
            sumada no dice de cuál viene ni si son comparables. El desglose por
            documento vive en el cajón, que es donde se puede accionar. */}
      </td>

      {/* LAS DOS CIFRAS, SIEMPRE (Brandon, 9-sep). Antes el on hand solo
          aparecía cuando difería, y esconderlo cuando coincide obliga a saberse
          la regla para leer la celda: un hueco no dice «son iguales», dice
          «no sé». La correcta sigue siendo la libre — por eso va grande y en
          color, y el on hand debajo, en gris y rotulado. */}
      <td className="px-3 py-2.5 text-right">
        <div className="flex items-baseline justify-end gap-1.5"
             title="Free to use: las piezas que se pueden vender hoy. Es la cifra correcta.">
          <span className="text-[9px] font-bold uppercase tracking-wide text-slate-400">free</span>
          <span className={`font-bold tabular-nums ${
            piezas === null ? "text-slate-300" : piezas > 0 ? "text-emerald-700" : "text-rose-600"}`}>
            {num(piezas)}
          </span>
        </div>
        <div className="flex items-baseline justify-end gap-1.5"
             title={f.reservado
               ? `On hand: ${num(f.stock_fisico)} piezas están físicamente en bodega, pero ${num(f.reservado)} están comprometidas en pedidos. Métrica de trackeo — lo vendible es lo free.`
               : "On hand: las piezas físicamente en bodega. Métrica de trackeo."}>
          <span className="text-[9px] font-bold uppercase tracking-wide text-slate-300">on hand</span>
          <span className={`tabular-nums ${
            f.stock_fisico !== piezas ? "font-semibold text-slate-600" : "text-slate-400"}`}>
            {num(f.stock_fisico)}
          </span>
        </div>
        {/* De lo pendiente, en la tabla queda SOLO el número de recepciones
            abiertas (Brandon, 9-sep). Las piezas sin recibir y los días se
            fueron al cajón: la cifra sumada aplana varias órdenes de compra en
            un número que no se puede accionar desde aquí, y los días cuentan
            desde un papel cuya fecha programada ya venció en todos los casos.
            El desglose por documento sigue completo al abrir el SKU. */}
        {!!f.recepcion_docs && (
          <div
            className="text-[10px] font-semibold text-amber-700"
            title="Documentos de recepción abiertos en Odoo y sin validar. Ábrelo para ver cuántas piezas trae cada uno, de qué orden de compra viene y cuánto lleva sin validarse."
          >
            {f.recepcion_docs > 1
              ? `${f.recepcion_docs} recepciones abiertas`
              : "1 recepción abierta"}
          </div>
        )}
        {/* La etiqueta «N no vendibles» se quitó de la tabla (Eduardo, 8-sep). No
            era un descuadre: son las piezas en CUARENTENA y SCRAP, que Odoo ya
            excluye de `qty_available`, así que nunca estuvieron sumadas en la
            cifra de arriba. Medido el 8-sep: 231 SKUs y 10,827 piezas —10,577 de
            ellas en cuarentena—, y en 138 SKUs es TODO su stock. Colgarla en rojo
            debajo de un cero se leía como si le faltaran piezas a la cifra.
            El dato sigue viajando en `no_vendible` y se sigue viendo por
            ubicación en el cajón («Dónde está»), que es donde se puede accionar:
            ahí se sabe en qué rack está la pieza que no se puede vender. */}
      </td>

      <td className="px-3 py-2.5 text-right tabular-nums text-slate-500">{num(f.reservado)}</td>

      {/* UBICACIÓN: EL ALMACÉN PRIMERO y el rack debajo (Brandon, 9-sep) — antes
          era al revés. La pregunta que se hace de un vistazo es «en cuál de las
          tres bodegas está», no «en qué rack»: el rack solo sirve cuando ya
          fuiste a la bodega correcta, y además NO la identifica — los 297
          nombres de rack de DROP OFF están todos repetidos en TEXCO. El rack no
          se borra, baja de renglón. Se listan TODAS las bodegas del SKU, no solo
          la principal: 71 SKUs viven a la vez en TEXCO y DROP OFF. */}
      <td className="px-3 py-2.5">
        {f.bodegas.length > 0 ? (
          <>
            <div className="flex flex-wrap items-center gap-1">
              {f.bodegas.map((b) => <Almacen key={b} nombre={b} />)}
            </div>
            {f.rack ? (
              <div className="mt-1 flex items-center gap-1 font-mono text-[11px] text-slate-500">
                <MapPin className="h-3 w-3 shrink-0 text-slate-300" />
                {f.rack}
                {f.n_ubicaciones > 1 && (
                  <span className="font-sans text-[10px] text-slate-400">
                    +{f.n_ubicaciones - 1} más
                  </span>
                )}
              </div>
            ) : (
              <div className="mt-1 text-[10px] text-amber-700"
                   title="Está en el almacén pero en la raíz o en zona de paso: nadie le asignó una posición.">
                sin rack asignado
              </div>
            )}
          </>
        ) : f.recepcion_piezas ? (
          // Si hay recepción abierta, «sin ubicación» sonaba a dato faltante y
          // contradecía la celda de Piezas. Es la MISMA noticia: no ha llegado.
          <span className="text-xs text-amber-700" title="No tiene ubicación porque todavía no se recibe en almacén">
            no recibido
          </span>
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
          {PUNTOS.map(({ clave, icono: Icono }) => {
            const p = f.validacion_bodega.puntos.find((x) => x.clave === clave);
            if (!p) return null;
            return (
              <span key={clave}
                    title={`${p.titulo}: ${p.etiqueta}${p.detalle ? ` — ${p.detalle}` : ""}`}
                    className={`inline-flex h-6 w-6 items-center justify-center rounded-md border ${ESTILO_PUNTO[p.estado]}`}>
                <Icono className="h-3.5 w-3.5" />
              </span>
            );
          })}
          <span
            title={f.validacion_bodega.validado
              ? "Cumple los cuatro requisitos de bodega"
              : `Falta: ${f.validacion_bodega.faltantes.join(", ")}`}
            className={`ml-1 rounded px-1.5 py-0.5 text-[10px] font-bold tabular-nums ${
              f.validacion_bodega.validado
                ? "bg-emerald-100 text-emerald-700"
                : "bg-slate-100 text-slate-500"}`}
          >
            {f.validacion_bodega.cumplidos}/{f.validacion_bodega.total}
          </span>
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
                {fila.comercial.validado.estado === "listo" && (
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
          <Recorrido fila={fila} />
          <PorRecibirse fila={fila} movs={movs} cargando={cargando} />
          <CotejoCajasBloque fila={fila} />
          <DondeEsta fila={fila} />
          <ValidacionBodega fila={fila} />
          <Comercial fila={fila} />

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
        {paso("Cajas", numCajas(fila.cajas), "libres en piso")}
        {signo("×")}
        {paso("Piezas / caja", fila.piezas_por_caja === null ? "—" : String(fila.piezas_por_caja),
          "caja master de Odoo")}
        {signo("=")}
        {/* La cadena CIERRA: cajas sale de dividir estas mismas piezas libres
            entre el factor. Antes este paso enseñaba el on hand y las cajas
            salían del on hand también, pero la tabla mostraba lo libre — el
            cajon y la tabla decían cosas distintas de la misma etiqueta. */}
        {paso("Piezas", num(fila.stock_odoo), "libres · free to use", true)}
      </div>
      {fila.stock_fisico !== null && fila.stock_fisico !== fila.stock_odoo && (
        <p className="mt-1.5 text-[11px] text-slate-400">
          On hand {num(fila.stock_fisico)} piezas — {num(fila.reservado)} comprometidas en
          pedidos. El on hand es métrica de trackeo; lo vendible es lo libre.
        </p>
      )}
      {fila.embarque && (
        <p className="mt-1 text-[11px] text-slate-400">Embarque {fila.embarque}.</p>
      )}
    </section>
  );
}

/**
 * EL COTEJO DE CAJAS — tres preguntas distintas, no tres versiones de una.
 *
 * Brandon, 8-sep: «necesitamos el dato que nos entrega almacén para realizar la
 * comparativa; tiene más importancia lo que nos da almacén en cuestión de cajas».
 * El dato de almacén MANDA — y hoy no existe en ningún sistema. Se barrió el
 * repo, los 16 esquemas de kubera, Odoo por XML-RPC y las 85 tablas de
 * WordPress: en Odoo `product.packaging` tiene CERO registros, hay 3
 * `stock.quant.package` en 36,256 quants, y 0 de 1,264 recepciones validadas
 * traen bultos. Se pinta igual, vacío y en ámbar, porque un hueco rotulado se
 * puede exigir y una columna ausente no.
 *
 * Y las otras dos NO SE RESTAN: la del packing list es el EMBARQUE y la de Odoo
 * es el PISO de hoy. TEC-0008-AMR trae 200 cajas de packing list y 5 piezas
 * físicas: no es un descuadre, es que ya se vendieron.
 */
function CotejoCajasBloque({ fila }: { fila: FilaInventario }) {
  const k = fila.cotejo_cajas;
  if (!k) return null;   // backend viejo: no se pinta, no se rompe.
  const tarjeta = (
    titulo: string, valor: string, sub: string, tono: string, manda?: boolean,
  ) => (
    <div className={`flex-1 rounded-xl border p-3 ${tono}`}>
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] font-bold uppercase tracking-[0.06em] opacity-70">
          {titulo}
        </span>
        {manda && (
          <span className="rounded bg-amber-200/70 px-1 text-[9px] font-bold uppercase text-amber-900">
            manda
          </span>
        )}
      </div>
      <div className="mt-1 text-xl font-extrabold tabular-nums">{valor}</div>
      <div className="mt-0.5 text-[11px] leading-tight opacity-70">{sub}</div>
    </div>
  );
  return (
    <section>
      <h3 className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
        Cotejo de cajas
      </h3>
      <div className="mt-2 flex gap-1.5">
        {tarjeta("Bodega", "—", "canal no construido: almacén todavía no tiene por dónde mandarlo",
          "border-amber-200 bg-amber-50 text-amber-800", true)}
        {tarjeta("Packing list", numCajas(k.packing_list),
          k.packing_list === null
            ? "ni renglón registrado ni cifra en costos_validados"
            : k.pl_fuente === "renglon"
              ? `leída del renglón ${k.pl_renglones?.join(", ")}${
                  k.pl_compartida ? ` · cartón compartido entre ${k.pl_renglones_carton} renglones` : ""}`
              : "cifra congelada de costos_validados (mayo/junio)",
          k.pl_fuente === "renglon"
            ? "border-emerald-200 bg-emerald-50 text-emerald-900"
            : "border-slate-200 bg-white text-slate-900")}
        {tarjeta("Odoo", numCajas(k.odoo),
          k.odoo === null ? "sin piezas libres que llenen caja" : "derivada de las piezas libres",
          "border-slate-200 bg-white text-slate-900")}
      </div>
      {k.pl_fuente === "renglon" && (
        <div className="mt-1.5 rounded-lg bg-slate-50 p-2 text-[11px] text-slate-500">
          <div>
            <b>De dónde sale:</b> renglón{k.pl_renglones && k.pl_renglones.length > 1 ? "es" : ""}{" "}
            <b className="font-mono">{k.pl_renglones?.join(", ")}</b> de{" "}
            <span className="font-mono">{k.pl_archivo}</span>
            {k.pl_piezas !== null && <> · {num(k.pl_piezas)} piezas según el packing list</>}
          </div>
          {/* El renglón lo resolvió la escalera de detección de imagen de la
              pestaña Costos y quedó guardado; aquí solo se abre el archivo. */}
          <div className="opacity-75">
            El renglón lo identificó la validación de costos (foto de Odoo → dHash →
            título → foto de ML + IA) y quedó registrado; aquí solo se lee la columna
            de cartones del archivo.
          </div>
          {k.pl_compartida && (
            <div className="mt-0.5 font-semibold text-amber-700">
              Cartón COMPARTIDO entre {k.pl_renglones_carton} renglones: la caja no es
              toda de este SKU, y por eso no se suma una por renglón.
            </div>
          )}
          {k.pl_congelado !== null && (
            <div className="mt-0.5 font-semibold text-rose-700">
              costos_validados dice {numCajas(k.pl_congelado)} cajas y el renglón dice{" "}
              {numCajas(k.packing_list)}. Manda el renglón: la columna es un congelado
              de mayo/junio.
            </div>
          )}
        </div>
      )}

      <p className="mt-1.5 text-[11px] text-slate-400">
        {k.estado === "cotejable"
          ? "El packing list es el EMBARQUE y Odoo es el PISO de hoy: no se restan. La diferencia normal es lo que ya se vendió."
          : k.estado === "solo_pl"
            ? "Solo hay la del embarque: no queda piso libre que contar."
            : k.estado === "solo_odoo"
              ? "Este SKU no trae cajas en costos_validados — la columna solo se llenó en las cargas de mayo y junio."
              : "Ni packing list ni piso libre: no hay nada que cotejar todavía."}
      </p>
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
                  <Almacen nombre={u.bodega} />{" "}
                  <span className="font-mono text-xs text-slate-700">
                    {u.rack || (u.es_stage ? "zona de paso" : "sin rack")}
                  </span>
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
            <b>{num(fila.recepcion_piezas)} piezas que todavía no están en bodega.</b>{" "}
            Siguen en{" "}
            {fila.recepcion_docs > 1
              ? `${fila.recepcion_docs} documentos de recepción abiertos en Odoo`
              : "un documento de recepción abierto en Odoo"}
            {fila.recepcion_dias !== null && `, el más viejo desde hace ${fila.recepcion_dias} días`}
            {fila.recepcion_ref && ` (${fila.recepcion_ref})`}, y nadie los ha
            validado. Por eso este SKU no tiene ubicación: no son lugares, son
            papeles pendientes.
          </p>
        )}
      </div>
    </section>
  );
}

/**
 * VALIDADO BODEGA: los cuatro requisitos que definió Brandon el 7-sep-2026.
 * Un producto NO está validado si le falta uno solo.
 *
 * Los puntos 3 y 4 dependen de canales que hoy no existen —bodega manda la foto
 * por Slack, y el Excel de specs no tiene formato decidido— así que salen en
 * `espera` y no en `falta`. La diferencia no es cosmética: `falta` culpa al
 * producto, `espera` dice que el sistema todavía no tiene por dónde recibirlo.
 * Mientras specs siga sin definirse, NINGÚN producto puede quedar validado del
 * todo, que es exactamente lo que se pidió.
 */
function ValidacionBodega({ fila }: { fila: FilaInventario }) {
  const v = fila.validacion_bodega;
  return (
    <section>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
          Validado bodega
        </h3>
        <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-bold ${
          v.validado ? "bg-emerald-100 text-emerald-700"
                     : "bg-slate-100 text-slate-500"}`}>
          {v.validado ? "VALIDADO" : `${v.cumplidos} de ${v.total} requisitos`}
        </span>
      </div>

      <div className="mt-2 space-y-1.5">
        {PUNTOS.map(({ clave, icono: Icono }) => {
          const p = fila.validacion_bodega.puntos.find((x) => x.clave === clave);
          if (!p) return null;
          return (
            <div key={clave}
                 className={`flex items-start gap-3 rounded-xl border p-2.5 ${ESTILO_PUNTO[p.estado]}`}>
              <Icono className="mt-0.5 h-4 w-4 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-sm font-bold">{p.titulo}</span>
                  <span className="text-xs font-semibold">{p.etiqueta}</span>
                </div>
                {p.detalle && <div className="text-[11px] opacity-80">{p.detalle}</div>}

                {/* El stock lleva las DOS cifras que pidió Brandon: «a la mano»
                    es lo que está físicamente y «disponible» lo que queda libre
                    después de reservas. Cuando difieren, la diferencia ES la
                    noticia. */}
                {clave === "stock" && p.mano !== undefined && (
                  <div className="mt-1 flex gap-3 text-[11px]">
                    <span>A la mano <b className="tabular-nums">{num(p.mano)}</b></span>
                    <span>Disponible <b className="tabular-nums">{num(p.disponible)}</b></span>
                    {!!p.mano && p.disponible === 0 && (
                      <span className="font-semibold">todo reservado</span>
                    )}
                  </div>
                )}

                {/* La foto solo se valida contra bodega cuando hay variantes:
                    una imagen genérica no distingue cuál de ellas es. */}
                {clave === "foto" && fila.variantes_odoo.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1">
                    {fila.variantes_odoo.map((v2) => (
                      <span
                        key={v2.sku}
                        title={`${v2.nombre}${v2.relacion === "plantilla"
                          ? " · misma plantilla en Odoo"
                          : " · solo comparte código base, NO son variantes en Odoo"}`}
                        className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-bold ${
                          v2.relacion === "plantilla"
                            ? "bg-white/70 ring-1 ring-current"
                            : "bg-white/40 opacity-70"}`}
                      >
                        {v2.sku}
                        {v2.relacion === "codigo" && " ·código"}
                      </span>
                    ))}
                  </div>
                )}

                <div className="mt-0.5 font-mono text-[10px] opacity-50">{p.fuente}</div>
              </div>
            </div>
          );
        })}
      </div>

      {!v.validado && (
        <p className="mt-1.5 text-[11px] text-slate-400">
          No se puede dar por validado sin los cuatro. Falta:{" "}
          <b>{v.faltantes.join(", ")}</b>.
        </p>
      )}

      {fila.ultimo_paso && (
        <p className="mt-1 text-[11px] text-slate-400">
          Último paso en el panel: {fila.ultimo_paso.accion}
          {fila.ultimo_paso.actor ? ` · ${fila.ultimo_paso.actor}` : ""}
          {fila.ultimo_paso.fecha ? ` · ${fila.ultimo_paso.fecha.slice(0, 10)}` : ""}
        </p>
      )}
    </section>
  );
}

/** Costo validado y envío a marketplace: NO son validación de almacén, así que
 *  van aparte y en compacto para no competir con los cuatro requisitos. */
function Comercial({ fila }: { fila: FilaInventario }) {
  return (
    <section>
      <h3 className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
        Otros estados
      </h3>
      <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
        {ETAPAS.map(({ clave, titulo, icono: Icono }) => {
          const e = fila.comercial[clave];
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

/**
 * EL RECORRIDO DE LA PIEZA — debió llegar → llegó → hay.
 *
 * Brandon, 9-sep: «cuántas piezas debieron haber llegado según el packing list,
 * cuántas llegaron realmente y cuántas actualmente hay disponibles».
 *
 * LO QUE ESTE BLOQUE TIENE PROHIBIDO HACER ES RESTAR LAS DOS ÚLTIMAS. La
 * diferencia entre lo recibido y lo que hay casi nunca es una merma: son
 * VENTAS. `TEC-0370-NEG` recibió 168 piezas en 8 documentos desde diciembre y
 * hoy tiene 8 — pintar «−160» ahí sería acusar un faltante inexistente. Por eso
 * lo salido se NOMBRA aparte y en gris, y la única resta que se hace es la del
 * packing list contra lo que ya entró.
 *
 * Y LA COBERTURA SE DICE. El packing list solo cubre los renglones que se
 * pudieron empatar: seis de los nueve del piloto cuadran EXACTO contra lo que
 * Odoo pidió —eso es lo que valida el método— pero tres se quedan cortos. En
 * esos, «debió llegar» es un PISO y así se rotula: decir que faltan piezas
 * cuando lo que falta es el renglón sería inventar un descuadre.
 */
function Recorrido({ fila }: { fila: FilaInventario }) {
  const r = fila.recorrido;
  if (!r) return null;
  const cob = r.cobertura_pl;

  const paso = (
    t: string, v: string, sub: string, tono: string, chico?: string,
  ) => (
    <div className={`flex-1 rounded-xl border p-3 ${tono}`}>
      <div className="text-[10px] font-bold uppercase tracking-[0.06em] opacity-70">{t}</div>
      <div className="mt-1 text-xl font-extrabold tabular-nums">{v}</div>
      <div className="mt-0.5 text-[11px] leading-tight opacity-70">{sub}</div>
      {chico && <div className="mt-0.5 text-[10px] opacity-60">{chico}</div>}
    </div>
  );
  const flecha = (
    <span className="self-center px-1 text-sm font-bold text-slate-300">→</span>
  );

  return (
    <section>
      <h3 className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
        Recorrido de la pieza
      </h3>
      <div className="mt-2 flex gap-1">
        {paso("Debió llegar",
          r.debio_llegar === null ? "—" : num(r.debio_llegar),
          r.debio_llegar === null
            ? "sin renglón del packing list"
            : cob !== null && !r.pl_completo
              ? `piso: los renglones cubren el ${Math.round(cob * 100)}%`
              : "según el packing list",
          r.debio_llegar === null
            ? "border-slate-200 bg-white text-slate-400"
            : r.pl_completo
              ? "border-emerald-200 bg-emerald-50 text-emerald-900"
              : "border-amber-200 bg-amber-50 text-amber-900")}
        {flecha}
        {paso("Llegó", num(r.llego),
          r.llego > 0
            ? `en ${r.documentos} ${r.documentos === 1 ? "recepción validada" : "recepciones validadas"}`
            : "nunca se ha recibido nada",
          r.llego > 0
            ? "border-slate-200 bg-white text-slate-900"
            : "border-rose-200 bg-rose-50 text-rose-800",
          r.llego > 0 && r.ultima_entrada
            ? `última el ${r.ultima_entrada.slice(0, 10)}`
            : r.pendiente > 0
              ? `${num(r.pendiente)} esperan sin validar`
              : undefined)}
        {flecha}
        {paso("Hay disponible", num(r.disponible), "free to use, hoy",
          r.disponible > 0
            ? "border-indigo-200 bg-indigo-50 text-indigo-900"
            : "border-slate-200 bg-white text-slate-400",
          r.a_la_mano !== r.disponible ? `${num(r.a_la_mano)} on hand` : undefined)}
      </div>

      {/* Lo salido se NOMBRA, jamás se pinta como faltante. */}
      {r.salido !== null && (
        <p className="mt-1.5 text-[11px] text-slate-400">
          De lo recibido ya salieron <b>{num(r.salido)}</b> piezas — ventas y envíos,
          no una merma. La resta entre «llegó» y «hay» no es un descuadre.
        </p>
      )}
      {cob !== null && !r.pl_completo && (
        <p className="mt-1.5 text-[11px] text-amber-700">
          Los renglones empatados del packing list suman <b>{num(r.debio_llegar)}</b> piezas
          y Odoo espera <b>{num(r.pedido_odoo)}</b>: falta empatar renglón, no falta
          mercancía. Por eso no se pinta un faltante.
        </p>
      )}
      {r.debio_llegar !== null && r.pl_completo && r.llego === 0 && r.pendiente > 0 && (
        <p className="mt-1.5 text-[11px] text-slate-400">
          El packing list cuadra EXACTO con lo que Odoo espera, y no ha entrado
          nada: todo sigue en recepciones sin validar.
        </p>
      )}
    </section>
  );
}

/**
 * POR RECIBIRSE — cuántas piezas tiene prometidas el SKU y en qué documentos.
 *
 * Pedido de Brandon el 8-sep: «cuando abro un sku me muestra la cantidad de
 * stock que está por recibirse».
 *
 * DOS COSAS QUE ESTE BLOQUE TIENE PROHIBIDO DECIR:
 *
 * 1. «En camino». Son documentos que Odoo tiene ABIERTOS y nadie validó; no hay
 *    ninguna señal de que la mercancía se haya movido. Las 30 recepciones
 *    huérfanas de mayo-junio siguen vivas y envenenan `incoming_qty` de 2,837
 *    SKUs — el 30% de los padres.
 * 2. Una fecha de llegada. Odoo solo da `scheduled_date` y en los 30 documentos
 *    abiertos esa fecha ya pasó en el 100% de los casos. Por eso aquí se pinta
 *    la EDAD del papel y no una promesa.
 *
 * El corte de 30 días separa el papel vivo del abandonado. No es adorno: un
 * documento de 118 días y uno de ayer describen situaciones opuestas y la suma
 * los aplana.
 */
function PorRecibirse({
  fila, movs, cargando,
}: {
  fila: FilaInventario;
  movs: MovimientosResp | null;
  cargando: boolean;
}) {
  const docs = movs?.pendientes ?? [];
  const piezas = docs.reduce((a, d) => a + d.piezas, 0);

  // Sin recepciones abiertas no se pinta nada: un bloque en cero es ruido.
  if (!cargando && !docs.length && !fila.recepcion_piezas) return null;

  const viejos = docs.filter((d) => (d.creado_dias ?? 0) > 30);
  return (
    <section>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
          <FileClock className="h-3.5 w-3.5" />
          Por recibirse
        </h3>
        {!cargando && (
          <span className="text-[11px] text-amber-700">
            {docs.length} {docs.length === 1 ? "documento abierto" : "documentos abiertos"}
          </span>
        )}
      </div>

      <div className="mt-2 rounded-xl border border-amber-200 bg-amber-50 p-3">
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-extrabold tabular-nums text-amber-800">
            {cargando ? "…" : num(piezas)}
          </span>
          <span className="text-xs font-semibold text-amber-800">piezas prometidas</span>
          {!cargando && !!fila.cajas_por_llegar && (
            <span className="text-[11px] text-amber-700">
              ≈ {numCajas(fila.cajas_por_llegar)} cajas
            </span>
          )}
        </div>
        <p className="mt-0.5 text-[11px] text-amber-800/80">
          Ninguna ha entrado a bodega: son documentos abiertos en Odoo, sin validar.
          No es mercancía en camino — no hay señal de que se haya movido.
        </p>

        {!cargando && docs.length > 0 && (
          <div className="mt-2 space-y-1 border-t border-amber-200 pt-2">
            {docs.map((d) => (
              <div key={d.documento}
                   className="flex flex-wrap items-baseline gap-x-2 text-[11px] text-amber-900">
                <span className="font-mono font-bold">{d.documento}</span>
                <span className="font-bold tabular-nums">{num(d.piezas)} pzas</span>
                <span className="opacity-70">
                  {d.orden_compra && `OC ${d.orden_compra} · `}
                  {d.creado_dias !== null && `${d.creado_dias} d de creado`}
                </span>
                {(d.creado_dias ?? 0) > 30 && (
                  <span className="rounded bg-amber-200/80 px-1.5 py-0.5 text-[10px] font-bold">
                    sin validar hace {d.creado_dias} d
                  </span>
                )}
                {d.sku_en_parcial && (
                  <span className="rounded bg-white/70 px-1.5 py-0.5 text-[10px] font-bold">
                    entró en la parcial: {num(d.sku_recibido)} de {num(d.sku_pedido)}
                  </span>
                )}
              </div>
            ))}
            {viejos.length > 0 && (
              <p className="pt-1 text-[11px] text-amber-800/80">
                {viejos.length === docs.length
                  ? "Todos llevan más de 30 días sin validar: papel abandonado, no entrega próxima."
                  : `${viejos.length} de ${docs.length} llevan más de 30 días sin validar.`}
              </p>
            )}
          </div>
        )}
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
        <p className="mt-2 rounded-xl border border-slate-200 bg-white px-3 py-6 text-center text-xs text-slate-400">
          Sin movimientos registrados en Odoo para este SKU.
          {!!movs?.pendientes?.length && (
            <span className="mt-1 block font-semibold text-amber-700">
              Tiene {movs.pendientes?.length}{" "}
              {movs.pendientes?.length === 1
                ? "recepción abierta" : "recepciones abiertas"}
              {" "}sin validar — míralas en el historial completo.
            </span>
          )}
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
  // Por omisión, TODO el histórico. A 90 días `TEC-0370-NEG` enseñaba 6 de sus
  // 285 movimientos y a 30 días ninguno: esconder el 96% de la historia en una
  // pantalla que existe para auditar es lo contrario de lo que se quiere. El
  // saldo corriente además solo se entiende desde el primer movimiento.
  const [dias, setDias] = useState(0);
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

        {!!movs?.pendientes?.length && <Pendientes filas={movs.pendientes} />}
        {!!movs?.compras?.length && <Compras filas={movs.compras} />}

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
        ) : !movs ? (
          // No es lo mismo «no hay» que «no llegó»: si la petición se abortó
          // (una recarga a medias, por ejemplo) decirlo evita concluir que un
          // SKU con 285 movimientos no tiene ninguno.
          <p className="py-20 text-center text-sm text-slate-400">
            No se alcanzó a cargar el historial. Vuelve a abrirlo.
          </p>
        ) : !movs.movimientos.length ? (
          <p className="py-20 text-center text-sm text-slate-400">
            Sin movimientos de este tipo
            {movs.dias ? " en la ventana elegida" : ""}.
            {movs.total_historico > 0 && !movs.dias && " Prueba otro filtro."}
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

/**
 * Las recepciones ABIERTAS del SKU, arriba del libro.
 *
 * No son movimientos y por eso van aparte y no suman al saldo: son documentos
 * que Odoo tiene creados y nadie validó, así que la mercancía no ha entrado a
 * ninguna parte. Sin este bloque, un SKU que no ha llegado enseña un historial
 * vacío teniendo cientos de piezas prometidas — que es justo la pregunta con la
 * que la gente abre esta pantalla.
 *
 * Una fila por DOCUMENTO, no por renglón: las 992 piezas de `JUGU-1153-MET` son
 * 214 renglones de `stock.move`, y volcarlos enterraría todo lo demás.
 */
function Pendientes({ filas }: { filas: RecepcionPendiente[] }) {
  const piezas = filas.reduce((a, f) => a + f.piezas, 0);
  return (
    <section className="mx-6 mt-4 rounded-xl border border-amber-200 bg-amber-50/60">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-amber-200 px-4 py-2.5">
        <h3 className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.06em] text-amber-800">
          <FileClock className="h-3.5 w-3.5" />
          Recepciones abiertas · {filas.length}{" "}
          {filas.length === 1 ? "documento" : "documentos"}
        </h3>
        <span className="text-xs text-amber-700">
          {num(piezas)} piezas prometidas · <b>ninguna ha entrado a bodega</b>
        </span>
      </header>

      <div className="divide-y divide-amber-200/70">
        {filas.map((f) => {
          // Cuando el documento se creó MUCHO después de su fecha programada,
          // «vencido hace N días» describe mal un papel recién nacido. Se avisa.
          const retro =
            f.creado_dias !== null && f.programado_dias !== null
            && f.programado_dias - f.creado_dias > 30;
          return (
            <div key={f.documento}
                 className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5">
              <span className="w-40 shrink-0 font-mono text-xs font-bold text-amber-900">
                {f.documento}
              </span>
              <span className="w-28 shrink-0 text-right text-sm font-bold tabular-nums text-amber-900">
                {num(f.piezas)} pzas
                <span className="ml-1 text-[10px] font-normal opacity-70">
                  · {f.renglones} rengl.
                </span>
              </span>
              <div className="min-w-0 flex-1 text-[11px] leading-snug text-amber-800">
                <div>
                  Creado {f.creado.slice(0, 10)}
                  {f.creado_dias !== null && ` (hace ${f.creado_dias} d)`}
                  {f.creado_por && ` por ${f.creado_por}`}
                  {" · programado "}{f.programado.slice(0, 10)}
                  {f.programado_dias !== null && ` (hace ${f.programado_dias} d)`}
                </div>
                <div className="opacity-75">
                  {f.orden_compra && `OC ${f.orden_compra} · `}
                  {f.socio}
                  {f.destino && ` → ${f.destino}`}
                </div>

                {/* Las DOS preguntas de Brandon, y en este orden: ¿la orden
                    recibió parcial?, y ¿entró este producto? La segunda no se
                    deduce de la primera — P03364 recibió parcial el 28-ago y de
                    JUGU-1153-MET no entró ni una pieza. */}
                {f.oc_parcial ? (
                  <div className="mt-0.5">
                    <span className="font-semibold">
                      Recepción PARCIAL: la orden lleva {f.oc_validadas} de{" "}
                      {f.oc_recepciones} documentos validados
                    </span>
                    {f.oc_docs_validados.length > 0 && (
                      <span className="opacity-75">
                        {" ("}
                        {f.oc_docs_validados
                          .map((d) => `${d.documento} el ${d.validado.slice(0, 10)}`)
                          .join(", ")}
                        {")"}
                      </span>
                    )}
                    <div className={f.sku_en_parcial
                      ? "font-semibold text-emerald-700"
                      : "font-semibold text-rose-700"}>
                      {f.sku_en_parcial
                        ? `Este SKU SÍ entró: ${num(f.sku_recibido)} de ${num(f.sku_pedido)} piezas ya recibidas.`
                        : "Este SKU NO entró en esa parcial: 0 piezas recibidas."}
                    </div>
                  </div>
                ) : f.oc_recepciones > 1 ? (
                  <div className="mt-0.5 opacity-75">
                    La orden tiene {f.oc_recepciones} recepciones y ninguna validada.
                  </div>
                ) : null}
                {retro && (
                  <div className="mt-0.5 font-semibold text-rose-700">
                    Se creó {(f.programado_dias ?? 0) - (f.creado_dias ?? 0)} días
                    DESPUÉS de su fecha programada: no lleva {f.programado_dias}{" "}
                    días esperando, lleva {f.creado_dias}.
                  </div>
                )}
              </div>
              <span className="shrink-0 rounded bg-white/70 px-1.5 py-0.5 text-[10px] font-bold uppercase text-amber-800 ring-1 ring-amber-300">
                {f.estado}
              </span>
            </div>
          );
        })}
      </div>

      <p className="border-t border-amber-200 px-4 py-2 text-[11px] text-amber-700">
        No son movimientos: <b>no mueven el saldo</b> ni aparecen en el libro de
        abajo. Son papeles que alguien tiene que validar en Odoo para que la
        mercancía entre.
      </p>
    </section>
  );
}

const ESTILO_VEREDICTO: Record<OrdenCompra["veredicto"], string> = {
  completa: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  parcial: "bg-amber-50 text-amber-800 ring-amber-200",
  nada: "bg-rose-50 text-rose-700 ring-rose-200",
  sobre: "bg-violet-50 text-violet-700 ring-violet-200",
};

const TEXTO_VEREDICTO: Record<OrdenCompra["veredicto"], string> = {
  completa: "Completa",
  parcial: "Parcial",
  nada: "Nada recibido",
  sobre: "Llegó de más",
};

/**
 * El histórico de COMPRA del SKU: qué se pidió y cuánto llegó.
 *
 * Complementa al bloque de recepciones abiertas, que solo dice qué papeles
 * están pendientes AHORA. La pregunta de bodega casi siempre es la comparación:
 * de lo que se compró, cuánto entró.
 *
 * `sobre` no es un caso teórico — `TEC-0008-AMR` recibió 201 de 200 pedidas.
 * Redondearlo a «completa» escondería una sobre-recepción, que es justo el tipo
 * de descuadre que alguien tendría que revisar.
 */
function Compras({ filas }: { filas: OrdenCompra[] }) {
  const pedido = filas.reduce((a, f) => a + f.pedido, 0);
  const recibido = filas.reduce((a, f) => a + f.recibido, 0);
  const pct = pedido ? Math.round((100 * recibido) / pedido) : 0;
  return (
    <section className="mx-6 mt-3 rounded-xl border border-slate-200 bg-white">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-slate-200 px-4 py-2.5">
        <h3 className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.06em] text-slate-500">
          <ScrollText className="h-3.5 w-3.5" />
          Órdenes de compra · {filas.length}
        </h3>
        <span className="text-xs text-slate-500">
          {num(recibido)} recibidas de {num(pedido)} compradas ·{" "}
          <b className={pct >= 100 ? "text-emerald-700"
            : pct > 0 ? "text-amber-700" : "text-rose-700"}>{pct}%</b>
        </span>
      </header>

      <div className="divide-y divide-slate-100">
        {filas.map((f) => (
          <div key={f.orden} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2.5">
            <span className="w-24 shrink-0 font-mono text-xs font-bold text-slate-800">
              {f.orden}
            </span>
            <span className="w-28 shrink-0 text-xs tabular-nums text-slate-400">
              {f.fecha.slice(0, 10)}
              {f.dias !== null && (
                <span className="block text-[10px]">hace {f.dias} d</span>
              )}
            </span>
            <span className="w-40 shrink-0 text-right text-sm tabular-nums text-slate-700">
              <b>{num(f.recibido)}</b> de {num(f.pedido)}
              {f.faltante > 0 && (
                <span className="block text-[10px] font-semibold text-rose-600">
                  faltan {num(f.faltante)}
                </span>
              )}
            </span>
            <span className={`shrink-0 rounded px-2 py-0.5 text-[11px] font-bold ring-1 ${ESTILO_VEREDICTO[f.veredicto]}`}>
              {TEXTO_VEREDICTO[f.veredicto]}
            </span>
            <div className="min-w-0 flex-1 text-[11px] leading-snug text-slate-400">
              <div>
                {f.recepciones_validadas} de {f.recepciones}{" "}
                {f.recepciones === 1 ? "recepción validada" : "recepciones validadas"}
                {f.proveedor && ` · ${f.proveedor}`}
              </div>
              {f.documentos.length > 0 && (
                <div className="font-mono opacity-80">
                  {f.documentos
                    .map((d) => `${d.documento}${d.estado === "done"
                      ? ` ✓ ${d.validado.slice(0, 10)}` : ` (${d.estado})`}`)
                    .join(" · ")}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </section>
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

      {/* Un saldo NEGATIVO es dato real, no un error de pantalla: significa que
          alguien ajustó por debajo de cero y el libro quedó imposible hasta que
          otro ajuste lo corrigió. Caso medido, TEC-0370-NEG: −18 sobre 6 piezas
          el 11-jul, en rojo hasta el +20 del 5-ago. Se marca para que se vea que
          la pantalla lo sabe. */}
      <span
        className={`w-20 shrink-0 text-right text-sm tabular-nums ${
          (m.saldo ?? 0) < 0 ? "font-bold text-rose-600" : "text-slate-500"}`}
        title={(m.saldo ?? 0) < 0
          ? "El libro quedó en negativo aquí: hubo un ajuste por debajo de cero"
          : undefined}
      >
        {num(m.saldo)}
      </span>

      <span className="w-32 shrink-0 truncate text-right text-xs text-slate-400">
        {m.quien || "—"}
      </span>
    </div>
  );
}
