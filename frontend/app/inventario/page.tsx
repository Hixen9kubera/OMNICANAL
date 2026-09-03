"use client";

/**
 * INVENTARIO · Catálogo Maestro
 *
 * Qué hay, dónde está, en qué caja vino y qué se movió. La carpeta ES la ruta;
 * `SesionGuard` ya lo monta `app/layout.tsx`, así que aquí NO va — pero
 * `AppNavbar` sí, porque el layout no lo pinta (mismo patrón que /costos).
 *
 * ES UNA VISTA DE LECTURA. No hay un solo botón que escriba stock, y es a
 * propósito: hoy la cadena es `Odoo → Woo → canales` y `stock_watch` copia el
 * `free_qty` de Odoo cada pasada. Un ajuste humano se revertiría solo en menos
 * de media hora, y la bitácora además culparía a Odoo de haberlo borrado. La
 * captura de entradas por packing list va aparte y con el dale de Brandon.
 *
 * TRES COSAS QUE ESTA PANTALLA TIENE PROHIBIDO DECIR, porque serían mentira:
 *   · "en tránsito" — el 99.7% de las recepciones abiertas de Odoo llevan
 *     3-4 meses vencidas y ninguna está programada a futuro. Aquí se dice
 *     "recepción abierta" con sus días encima.
 *   · "publicado" a secas de una variación cuyo padre está en borrador: no se
 *     ve en la tienda. Son 4,498 de 7,329 en el catálogo.
 *   · un solo número de contenedor cuando las dos fuentes discrepan.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Boxes, Camera, CheckCircle2, ChevronRight, Clock,
  Container, EyeOff, Layers, Loader2, PackageSearch, RefreshCw, Ship,
  Truck, X,
} from "lucide-react";

import AppNavbar from "@/components/AppNavbar";
import { listarInventario, mensajeDeError, movimientosInventario } from "@/lib/api";
import type {
  ClaveEtapa, EstadoEtapa, FilaInventario, InventarioResp, MovimientosResp,
} from "@/lib/types";

const COLOR = "#4F46E5";

/** Las cinco etapas, en el orden en que Brandon las nombró. */
const ETAPAS: { clave: ClaveEtapa; titulo: string; icono: typeof Camera }[] = [
  { clave: "en_proceso", titulo: "En proceso", icono: Clock },
  { clave: "fotos", titulo: "Fotos", icono: Camera },
  { clave: "variantes", titulo: "Variantes", icono: Layers },
  { clave: "validado", titulo: "Validado", icono: CheckCircle2 },
  { clave: "enviado_full", titulo: "Enviado FULL", icono: Truck },
];

/** Paleta por estado. La misma que el resto del panel: verde/ámbar/rosa/slate. */
const ESTILO_ETAPA: Record<EstadoEtapa, string> = {
  listo: "border-emerald-200 bg-emerald-50 text-emerald-700",
  parcial: "border-amber-200 bg-amber-50 text-amber-700",
  pendiente: "border-slate-200 bg-slate-50 text-slate-500",
  bloqueado: "border-rose-200 bg-rose-50 text-rose-700",
  na: "border-slate-200 bg-white text-slate-400",
};

const CAUSAS: { v: string; t: string }[] = [
  { v: "reales", t: "Movimientos reales" },
  { v: "entrada", t: "Entradas" },
  { v: "venta", t: "Ventas" },
  { v: "envio_full", t: "FULL / FBA" },
  { v: "devolucion", t: "Devoluciones" },
  { v: "ajuste", t: "Ajustes de conteo" },
  { v: "traspaso", t: "Traspasos" },
  { v: "merma", t: "Merma" },
  { v: "todo", t: "Todo (con pasos internos)" },
];

const ETIQUETA_CAUSA: Record<string, string> = {
  entrada: "Entrada", venta: "Venta", envio_full: "Envío FULL/FBA",
  devolucion: "Devolución", ajuste: "Ajuste", traspaso: "Traspaso",
  preparacion: "Preparación", merma: "Merma", cuarentena: "Cuarentena",
  otro: "Otro",
};

const COLOR_CAUSA: Record<string, string> = {
  entrada: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  devolucion: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  venta: "bg-slate-100 text-slate-600 ring-slate-200",
  envio_full: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  ajuste: "bg-amber-50 text-amber-700 ring-amber-200",
  traspaso: "bg-sky-50 text-sky-700 ring-sky-200",
  preparacion: "bg-slate-50 text-slate-400 ring-slate-200",
  merma: "bg-rose-50 text-rose-700 ring-rose-200",
  cuarentena: "bg-rose-50 text-rose-700 ring-rose-200",
};

const num = (v: number | null | undefined, guion = "—") =>
  v === null || v === undefined ? guion : Math.round(v).toLocaleString("es-MX");

export default function InventarioPage() {
  const [datos, setDatos] = useState<InventarioResp | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [skusInput, setSkusInput] = useState("");
  const [filtroSkus, setFiltroSkus] = useState<string[] | undefined>(undefined);
  const [alerta, setAlerta] = useState<string | null>(null);
  const [abierto, setAbierto] = useState<FilaInventario | null>(null);

  // Debounce del filtro por SKU: el mismo criterio que /costos (500 ms), y aquí
  // pesa más porque cada fila cruza Woo, Odoo y kubera en vivo.
  useEffect(() => {
    const t = setTimeout(() => {
      const l = skusInput.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
      setFiltroSkus(l.length ? l : undefined);
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
        // /costos se traga los errores de carga y la tabla queda vacía sin
        // decir por qué — es el síntoma exacto de los tres 403 de RBAC. Aquí
        // se muestran, como hace /monitoreo.
        setError(mensajeDeError(e, "No se pudo leer el inventario."));
      })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [filtroSkus]);

  useEffect(() => cargar(), [cargar]);

  const filas = useMemo(() => {
    const items = datos?.items ?? [];
    if (!alerta) return items;
    const pruebas: Record<string, (f: FilaInventario) => boolean> = {
      sin_alta: (f) => !f.existe_en_woo,
      sin_odoo: (f) => !f.existe_en_odoo,
      invisibles: (f) => f.invisible_en_tienda,
      descuadre: (f) => !!f.descuadre,
      recepcion_vencida: (f) => (f.recepcion_dias ?? 0) > 30,
      sin_fotos: (f) => f.etapas.fotos.estado === "pendiente",
      sin_costo: (f) => f.etapas.validado.estado === "pendiente",
      contenedor_discrepa: (f) => f.contenedor_discrepa,
      odoo_duplicado: (f) => f.odoo_duplicado,
    };
    return items.filter(pruebas[alerta] ?? (() => true));
  }, [datos, alerta]);

  const r = datos?.resumen;

  return (
    <div className="min-h-screen bg-[#f6f7fb]">
      <AppNavbar />
      <main className="mx-auto max-w-[1400px] px-4 py-6">
        <Encabezado
          resumen={r}
          esPiloto={datos?.es_piloto ?? true}
          cargando={cargando}
          onRecargar={cargar}
        />

        {error && (
          <div className="mt-4 flex items-start gap-2 rounded-xl bg-rose-50 p-3 text-sm text-rose-700 ring-1 ring-rose-200">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {r && <Kpis resumen={r} />}
        {r && (
          <BandaAlertas
            resumen={r}
            activa={alerta}
            onToggle={(k) => setAlerta((a) => (a === k ? null : k))}
          />
        )}

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <div className="relative">
            <PackageSearch className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              value={skusInput}
              onChange={(e) => setSkusInput(e.target.value)}
              placeholder="SKUs separados por coma (vacío = los 10 del piloto)"
              className="w-[430px] max-w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm outline-none focus:border-indigo-300"
            />
          </div>
          {filtroSkus && (
            <button
              type="button"
              onClick={() => setSkusInput("")}
              className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-500 hover:bg-slate-50"
            >
              Volver al piloto
            </button>
          )}
          <span className="text-xs text-slate-400">
            {filas.length} de {datos?.total ?? 0} SKUs
          </span>
        </div>

        <Tabla filas={filas} cargando={cargando} onAbrir={setAbierto} />

        <p className="mt-4 text-xs leading-relaxed text-slate-400">
          Todo se lee en vivo de WooCommerce, Odoo y kubera en cada carga — nada
          sale de caché. Esta pestaña no escribe stock en ninguna parte.
        </p>
      </main>

      {abierto && <Cajon fila={abierto} onCerrar={() => setAbierto(null)} />}
    </div>
  );
}

/* ─────────────────────────── encabezado y KPIs ─────────────────────────── */

function Encabezado({
  resumen, esPiloto, cargando, onRecargar,
}: {
  resumen?: InventarioResp["resumen"];
  esPiloto: boolean;
  cargando: boolean;
  onRecargar: () => void;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <p className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
          Catálogo maestro · Bodega Kubera
        </p>
        <h1 className="mt-1 flex items-center gap-2 text-2xl font-extrabold tracking-tight text-slate-900">
          <Boxes className="h-6 w-6" style={{ color: COLOR }} />
          Inventario
          {esPiloto && (
            <span className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-bold text-indigo-700 ring-1 ring-indigo-200">
              piloto · 10 SKUs
            </span>
          )}
        </h1>
        <p className="mt-1 max-w-3xl text-sm text-slate-500">
          Odoo es el maestro del inventario y su libro de movimientos es la
          única trazabilidad que existe. Esta vista lo lee — no lo modifica.
        </p>
      </div>
      <div className="flex items-center gap-3">
        {resumen && (
          <div className="text-right">
            <div className="text-2xl font-extrabold tabular-nums text-slate-900">
              {resumen.completos}
              <span className="text-sm font-bold text-slate-400"> / {resumen.skus}</span>
            </div>
            <div className="text-xs text-slate-500">con las 4 etapas cerradas</div>
          </div>
        )}
        <button
          type="button"
          onClick={onRecargar}
          disabled={cargando}
          className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-50"
        >
          <RefreshCw className={`h-4 w-4 ${cargando ? "animate-spin" : ""}`} />
          Actualizar
        </button>
      </div>
    </div>
  );
}

function Kpis({ resumen }: { resumen: InventarioResp["resumen"] }) {
  const tarjetas = [
    { t: "Disponible", v: resumen.disponible, p: "libre de venta en Odoo", i: Boxes, tono: "" },
    { t: "Reservado", v: resumen.reservado, p: "comprometido en pedidos", i: Layers, tono: "" },
    {
      t: "En recepción", v: resumen.en_recepcion, i: Ship, tono: "aviso",
      // El rótulo NO dice "en tránsito": ver la cabecera del archivo.
      p: resumen.alertas.recepcion_vencida
        ? `${resumen.alertas.recepcion_vencida} recepciones vencidas`
        : "recepciones abiertas",
    },
    { t: "FULL / FBA", v: resumen.full + resumen.fba, p: "en bodega del marketplace", i: Truck, tono: "" },
    {
      t: "Descuadres", v: resumen.alertas.descuadre, i: AlertTriangle,
      p: "Woo ≠ Odoo", tono: resumen.alertas.descuadre ? "peligro" : "",
    },
  ];
  const clase = (tono: string) =>
    tono === "aviso"
      ? "border-amber-200 bg-amber-50"
      : tono === "peligro"
        ? "border-rose-200 bg-rose-50"
        : "border-slate-200 bg-white shadow-[0_1px_3px_rgba(16,24,40,.06)]";
  const texto = (tono: string) =>
    tono === "aviso" ? "text-amber-800" : tono === "peligro" ? "text-rose-800" : "text-slate-900";

  return (
    <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
      {tarjetas.map((c) => (
        <div key={c.t} className={`rounded-2xl border p-4 ${clase(c.tono)}`}>
          <div className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
            <c.i className="h-3.5 w-3.5" />
            {c.t}
          </div>
          <div className={`mt-2 text-[28px] font-extrabold leading-none tracking-tight tabular-nums ${texto(c.tono)}`}>
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
    { k: "sin_alta", t: "Sin alta en Woo", n: resumen.alertas.sin_alta, tono: "peligro" },
    { k: "invisibles", t: "Publicada pero invisible", n: resumen.alertas.invisibles, tono: "peligro" },
    { k: "recepcion_vencida", t: "Recepción vencida", n: resumen.alertas.recepcion_vencida, tono: "aviso" },
    { k: "sin_fotos", t: "Sin fotos", n: resumen.alertas.sin_fotos, tono: "aviso" },
    { k: "sin_costo", t: "Sin costo", n: resumen.alertas.sin_costo, tono: "aviso" },
    { k: "descuadre", t: "Descuadre Woo ↔ Odoo", n: resumen.alertas.descuadre, tono: "peligro" },
    { k: "contenedor_discrepa", t: "Contenedor discrepa", n: resumen.alertas.contenedor_discrepa, tono: "" },
    { k: "sin_odoo", t: "Sin producto en Odoo", n: resumen.alertas.sin_odoo, tono: "" },
    { k: "odoo_duplicado", t: "Duplicado en Odoo", n: resumen.alertas.odoo_duplicado, tono: "peligro" },
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
          c.tono === "peligro"
            ? "border-rose-200 bg-rose-50 text-rose-700"
            : c.tono === "aviso"
              ? "border-amber-200 bg-amber-50 text-amber-800"
              : "border-slate-200 bg-white text-slate-600";
        return (
          <button
            key={c.k}
            type="button"
            onClick={() => onToggle(c.k)}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-bold transition ${base} ${
              on ? "ring-2 ring-indigo-300 ring-offset-1" : "hover:brightness-95"
            }`}
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

/* ────────────────────────────── la tabla ────────────────────────────── */

function Tabla({
  filas, cargando, onAbrir,
}: {
  filas: FilaInventario[];
  cargando: boolean;
  onAbrir: (f: FilaInventario) => void;
}) {
  return (
    <div className="mt-4 overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-[0_1px_3px_rgba(16,24,40,.06)]">
      <table className="w-full min-w-[1180px] text-sm">
        <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-400">
          <tr>
            <th className="w-14 px-3 py-3" />
            <th className="px-3 py-3 text-left font-bold">SKU · producto</th>
            <th className="px-3 py-3 text-left font-bold">Empaque</th>
            <th className="px-3 py-3 text-right font-bold">Cajas</th>
            <th className="px-3 py-3 text-right font-bold">Pzs/caja</th>
            <th className="px-3 py-3 text-right font-bold">Disponible</th>
            <th className="px-3 py-3 text-right font-bold">Reserv.</th>
            <th className="px-3 py-3 text-right font-bold">Woo ↔ Odoo</th>
            <th className="px-3 py-3 text-left font-bold">Etapas</th>
            <th className="px-3 py-3 text-right font-bold">Trazabilidad</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {cargando && !filas.length && (
            <tr>
              <td colSpan={10} className="px-4 py-14 text-center text-slate-400">
                <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                <p className="mt-2 text-sm">
                  Cruzando WooCommerce, Odoo y kubera en vivo…
                </p>
              </td>
            </tr>
          )}
          {!cargando && !filas.length && (
            <tr>
              <td colSpan={10} className="px-4 py-14 text-center text-sm text-slate-400">
                Sin resultados.
              </td>
            </tr>
          )}
          {filas.map((f) => (
            <Fila key={f.sku} f={f} onAbrir={onAbrir} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Fila({ f, onAbrir }: { f: FilaInventario; onAbrir: (f: FilaInventario) => void }) {
  return (
    <tr className="group cursor-pointer align-middle hover:bg-slate-50/70" onClick={() => onAbrir(f)}>
      <td className="px-3 py-2.5">
        {f.imagen ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={f.imagen}
            alt=""
            loading="lazy"
            className="h-11 w-11 rounded-lg border border-slate-200 object-cover"
          />
        ) : (
          <div className="flex h-11 w-11 items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50">
            <Camera className="h-4 w-4 text-slate-300" />
          </div>
        )}
      </td>

      <td className="max-w-[300px] px-3 py-2.5">
        <div className="flex items-center gap-2">
          <span className="font-mono text-[13px] font-bold text-slate-900">{f.sku}</span>
          {f.tipo === "variacion" && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold text-slate-500">
              variante
            </span>
          )}
          {f.es_padre && (
            <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-bold text-indigo-600">
              padre · {f.n_hijas}
            </span>
          )}
          {f.invisible_en_tienda && (
            <span
              title={`Publicada, pero su padre está en ${f.padre_status}: no se ve en la tienda`}
              className="inline-flex items-center gap-1 rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-bold text-rose-700"
            >
              <EyeOff className="h-3 w-3" /> invisible
            </span>
          )}
        </div>
        <div className="truncate text-xs text-slate-500">{f.nombre || "—"}</div>
      </td>

      <td className="px-3 py-2.5">
        {f.contenedor ? (
          <div className="flex items-center gap-1.5">
            <Container className="h-3.5 w-3.5 shrink-0 text-slate-300" />
            <div>
              <div className="font-mono text-xs text-slate-700">{f.contenedor}</div>
              <div className="text-[10px] text-slate-400">
                {f.embarque ? `embarque ${f.embarque}` : ""}
                {f.contenedor_es_booking && (
                  <span title="Es la referencia del transitario, no un contenedor ISO">
                    {" "}· booking
                  </span>
                )}
                {f.contenedor_discrepa && (
                  <span className="text-rose-600" title={`Odoo: ${f.contenedor_odoo} · costos: ${f.contenedor_costo}`}>
                    {" "}· discrepa
                  </span>
                )}
              </div>
            </div>
          </div>
        ) : (
          <span className="text-xs text-slate-300">—</span>
        )}
      </td>

      <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">{num(f.cajas)}</td>
      <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">
        {f.piezas_por_caja === null ? "—" : f.piezas_por_caja}
        {f.piezas_declaradas !== null && (
          <div className="text-[10px] text-slate-400">= {num(f.piezas_declaradas)} pzs</div>
        )}
      </td>

      <td className="px-3 py-2.5 text-right">
        <div className="font-semibold tabular-nums text-slate-900">{num(f.stock_odoo)}</div>
        {!!f.recepcion_piezas && (
          <div
            className="text-[10px] font-semibold text-amber-700"
            title={`Recepción ${f.recepcion_ref ?? ""} abierta desde ${f.recepcion_desde.slice(0, 10)}`}
          >
            +{num(f.recepcion_piezas)} en recepción
            {f.recepcion_dias !== null && ` · ${f.recepcion_dias}d`}
          </div>
        )}
      </td>

      <td className="px-3 py-2.5 text-right tabular-nums text-slate-500">{num(f.reservado)}</td>

      <td className="px-3 py-2.5 text-right">
        {f.descuadre === null ? (
          <span className="text-xs text-slate-300">—</span>
        ) : f.descuadre === 0 ? (
          <span className="text-xs text-emerald-600">cuadra</span>
        ) : (
          <span className="rounded bg-rose-50 px-1.5 py-0.5 text-xs font-bold tabular-nums text-rose-700">
            {f.descuadre > 0 ? "+" : ""}
            {f.descuadre}
          </span>
        )}
      </td>

      <td className="px-3 py-2.5">
        <div className="flex items-center gap-1">
          {ETAPAS.map(({ clave, titulo, icono: Icono }) => {
            const e = f.etapas[clave];
            return (
              <span
                key={clave}
                title={`${titulo}: ${e.etiqueta}${e.detalle ? ` — ${e.detalle}` : ""}`}
                className={`inline-flex h-6 w-6 items-center justify-center rounded-md border ${ESTILO_ETAPA[e.estado]}`}
              >
                <Icono className="h-3.5 w-3.5" />
              </span>
            );
          })}
        </div>
      </td>

      <td className="px-3 py-2.5 text-right">
        <span className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-600 group-hover:border-indigo-200 group-hover:text-indigo-600">
          Historial <ChevronRight className="h-3.5 w-3.5" />
        </span>
      </td>
    </tr>
  );
}

/* ─────────────────── el cajón lateral: ficha + historial ─────────────────── */

function Cajon({ fila, onCerrar }: { fila: FilaInventario; onCerrar: () => void }) {
  const [movs, setMovs] = useState<MovimientosResp | null>(null);
  const [causa, setCausa] = useState("reales");
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
    movimientosInventario(fila.sku, causa, 300, ctrl.signal)
      .then(setMovs)
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        setError(mensajeDeError(e, "No se pudo leer el historial."));
      })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  }, [fila.sku, causa]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-sm" onClick={onCerrar} />
      <aside className="relative flex h-full w-full max-w-2xl animate-slide-in flex-col bg-slate-50 shadow-2xl">
        <header className="flex items-start justify-between gap-3 border-b border-slate-200 bg-white px-5 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-bold text-slate-900">{fila.sku}</span>
              {fila.es_padre && (
                <span className="rounded bg-indigo-50 px-1.5 py-0.5 text-[10px] font-bold text-indigo-600">
                  padre · {fila.n_hijas} variantes
                </span>
              )}
              {fila.tipo === "variacion" && fila.padre_sku && (
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold text-slate-500">
                  variante de {fila.padre_sku}
                </span>
              )}
            </div>
            <p className="truncate text-sm text-slate-500">{fila.nombre || "—"}</p>
          </div>
          <button
            type="button"
            onClick={onCerrar}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
          >
            <X className="h-5 w-5" />
          </button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-5">
          <Jerarquia fila={fila} />
          <Etapas fila={fila} />

          <section>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
                Trazabilidad
              </h3>
              <select
                value={causa}
                onChange={(e) => setCausa(e.target.value)}
                className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-600 outline-none"
              >
                {CAUSAS.map((c) => (
                  <option key={c.v} value={c.v}>{c.t}</option>
                ))}
              </select>
            </div>

            {movs && movs.cuadra === false && (
              <div className="mt-2 flex items-start gap-2 rounded-lg bg-amber-50 p-2.5 text-xs text-amber-800 ring-1 ring-amber-200">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  El libro suma {num(movs.saldo_libro)} y Odoo publica{" "}
                  {num(movs.saldo_odoo)}. La diferencia es real y hay que
                  revisarla con Inventarios — no es un error de esta pantalla.
                </span>
              </div>
            )}

            {error && (
              <div className="mt-2 rounded-lg bg-rose-50 p-2.5 text-xs text-rose-700 ring-1 ring-rose-200">
                {error}
              </div>
            )}

            {cargando ? (
              <div className="py-10 text-center text-slate-400">
                <Loader2 className="mx-auto h-5 w-5 animate-spin" />
                <p className="mt-2 text-xs">Leyendo el libro de Odoo…</p>
              </div>
            ) : (
              <Historial movs={movs} />
            )}
          </section>
        </div>
      </aside>
    </div>
  );
}

function Jerarquia({ fila }: { fila: FilaInventario }) {
  const celda = (t: string, v: string, sub?: string) => (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      <div className="text-[10px] font-bold uppercase tracking-[0.06em] text-slate-400">{t}</div>
      <div className="mt-1 text-lg font-extrabold tabular-nums text-slate-900">{v}</div>
      {sub && <div className="text-[11px] text-slate-400">{sub}</div>}
    </div>
  );
  return (
    <section>
      <h3 className="text-[11px] font-bold uppercase tracking-[0.06em] text-slate-400">
        Jerarquía de empaque
      </h3>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {celda("Contenedor", fila.contenedor || "—",
          fila.embarque ? `embarque ${fila.embarque}` : undefined)}
        {celda("Cajas", num(fila.cajas))}
        {celda("Piezas / caja", fila.piezas_por_caja === null ? "—" : String(fila.piezas_por_caja))}
        {celda("Declaradas", num(fila.piezas_declaradas), "según packing list")}
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {celda("Disponible", num(fila.stock_odoo), "libre en Odoo")}
        {celda("Físico", num(fila.stock_fisico), "en racks")}
        {celda("Reservado", num(fila.reservado))}
        {celda("En Woo", num(fila.stock_woo),
          fila.descuadre ? `descuadre ${fila.descuadre > 0 ? "+" : ""}${fila.descuadre}` : "cuadra")}
      </div>
      {!!fila.recepcion_piezas && (
        <p className="mt-2 rounded-lg bg-amber-50 p-2.5 text-xs text-amber-800 ring-1 ring-amber-200">
          <Ship className="mr-1 inline h-3.5 w-3.5" />
          {num(fila.recepcion_piezas)} piezas en una recepción de Odoo que sigue
          ABIERTA {fila.recepcion_dias !== null && `desde hace ${fila.recepcion_dias} días`}
          {fila.recepcion_ref && ` (${fila.recepcion_ref})`}. No está programada
          a futuro: es una recepción sin validar, no mercancía en camino.
        </p>
      )}
      {fila.canales.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {fila.canales.map((c, i) => (
            <span
              key={`${c.canal}-${c.listing_id ?? i}`}
              className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-slate-600 ring-1 ring-slate-200"
            >
              {c.canal}
              {c.status ? ` · ${c.status}` : ""}
              {c.fulfillment ? " · FULL" : ""}
            </span>
          ))}
        </div>
      )}
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
            <div
              key={clave}
              className={`flex items-start gap-3 rounded-xl border p-2.5 ${ESTILO_ETAPA[e.estado]}`}
            >
              <Icono className="mt-0.5 h-4 w-4 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-sm font-bold">{titulo}</span>
                  <span className="text-xs font-semibold">{e.etiqueta}</span>
                </div>
                {e.detalle && <div className="text-[11px] opacity-80">{e.detalle}</div>}
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

function Historial({ movs }: { movs: MovimientosResp | null }) {
  if (!movs || !movs.movimientos.length) {
    return (
      <p className="py-8 text-center text-xs text-slate-400">
        Sin movimientos registrados en Odoo para este SKU.
      </p>
    );
  }
  return (
    <>
      <div className="mt-2 overflow-x-auto rounded-xl border border-slate-200 bg-white">
        <table className="w-full min-w-[560px] text-xs">
          <thead className="bg-slate-50 text-[10px] uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-2.5 py-2 text-left font-bold">Fecha</th>
              <th className="px-2.5 py-2 text-left font-bold">Concepto</th>
              <th className="px-2.5 py-2 text-left font-bold">Documento</th>
              <th className="px-2.5 py-2 text-right font-bold">Cant.</th>
              <th className="px-2.5 py-2 text-right font-bold">Saldo</th>
              <th className="px-2.5 py-2 text-left font-bold">Quién</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {movs.movimientos.map((m, i) => (
              <tr key={`${m.documento}-${m.fecha}-${i}`} className={m.interno ? "opacity-50" : ""}>
                <td className="whitespace-nowrap px-2.5 py-2 tabular-nums text-slate-500">
                  {m.fecha.slice(0, 10)}
                </td>
                <td className="px-2.5 py-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-bold ring-1 ${
                      COLOR_CAUSA[m.causa] ?? "bg-slate-100 text-slate-600 ring-slate-200"
                    }`}
                  >
                    {ETIQUETA_CAUSA[m.causa] ?? m.causa}
                  </span>
                  {m.contraparte && (
                    <div className="mt-0.5 text-[10px] text-slate-400">{m.contraparte}</div>
                  )}
                </td>
                <td className="px-2.5 py-2 font-mono text-[11px] text-slate-500">
                  {m.documento || "—"}
                  {m.referencia && <div className="text-[10px] text-slate-400">{m.referencia}</div>}
                </td>
                <td className="whitespace-nowrap px-2.5 py-2 text-right tabular-nums">
                  <span
                    className={
                      m.delta > 0 ? "font-bold text-emerald-700"
                        : m.delta < 0 ? "font-bold text-rose-700" : "text-slate-400"
                    }
                  >
                    {m.delta > 0 ? "+" : ""}
                    {m.delta === 0 ? "—" : num(m.delta)}
                  </span>
                  {m.pedido !== null && (
                    // Lo pedido ≠ lo hecho: es el 6.2% de los movimientos y de
                    // ahí salen los faltantes de embarque.
                    <div className="text-[10px] text-amber-700" title="Lo pedido no coincide con lo recibido">
                      pedidas {num(m.pedido)}
                    </div>
                  )}
                </td>
                <td className="px-2.5 py-2 text-right tabular-nums text-slate-600">
                  {num(m.saldo)}
                </td>
                <td className="px-2.5 py-2 text-[11px] text-slate-500">{m.quien || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-slate-400">
        {movs.movimientos.length} de {movs.total} movimientos · saldo del libro{" "}
        {num(movs.saldo_libro)}
        {movs.cuadra && " · cuadra con Odoo"}. Registro de Odoo: un error se
        corrige con un ajuste, no borrando el renglón.
      </p>
    </>
  );
}
