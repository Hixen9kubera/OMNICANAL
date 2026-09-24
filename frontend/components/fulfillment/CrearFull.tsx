"use client";

/**
 * FULLFILMENT · CREAR FULL — la primera pantalla de la pestaña (Brandon,
 * 24-sep-2026: "como las aplicaciones de banco: primero la transacción").
 *
 * Arriba el SALDO de la cuenta (lo que hay hoy en FULL, lo agotado, lo que va en
 * camino); abajo la PROPUESTA de la semana, SKU por SKU, que la persona corrige
 * antes de crear. La cuenta se elige aquí: un FULL es de UNA cuenta.
 *
 *   · la propuesta la calcula `proponer.ts` con los insumos de
 *     `GET /api/fulfillment/crear-full` (ventas FULL de 30 días, stock en FULL,
 *     en camino, borradores y libre en Odoo);
 *   · «Revisar y crear» pide la VISTA PREVIA con el libre de Odoo releído en ese
 *     momento: una orden por almacén, qué se recorta y por qué;
 *   · crear escribe la cotización en BORRADOR en Odoo SÓLO con el interruptor
 *     encendido (lo enciende un admin aquí mismo). Apagado, la vista previa es
 *     exactamente lo que se crearía, y la lista se puede descargar.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle, ArrowRight, Boxes, CheckCircle2, Download, ExternalLink, PackageX, Power, RotateCcw,
  Search, Truck,
} from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import { CUENTAS, csvDe, proponer } from "./proponer";
import type { EstadoPropuesta, Propuesta } from "./proponer";
import {
  BotonCerrar, Ceja, ChipCuenta, FONDO_RAYADO, PUNTO_CUENTA, Tarjeta, Ventana, dia, num, rangoSemana,
} from "./ui";
import type {
  Cuenta, FiltroCuenta, Interruptor, ParametrosFull, PropuestaFull, ResultadoCrear, Rol, StockHoy,
} from "./tipos";

type Filtro = "mandar" | "sin_odoo" | "cubiertos" | "todos";

const FILTROS: { k: Filtro; t: string; titulo: string }[] = [
  { k: "mandar", t: "Por mandar", titulo: "Lo que la propuesta manda, más lo que tú agregues a mano." },
  { k: "sin_odoo", t: "Sin stock en Odoo", titulo: "Venden en FULL y lo necesitan, pero Odoo no tiene libre: es señal de COMPRAS, no de FULL." },
  { k: "cubiertos", t: "Ya cubiertos", titulo: "Lo que hay en FULL, en camino y en borradores alcanza la cobertura." },
  { k: "todos", t: "Todos", titulo: "Todos los SKUs con venta FULL en los últimos 30 días." },
];

const EN_FILTRO: Record<Filtro, EstadoPropuesta[]> = {
  mandar: ["mandar", "tope_odoo"],
  sin_odoo: ["sin_odoo", "no_en_odoo"],
  cubiertos: ["cubierto", "bajo_minimo"],
  todos: ["mandar", "tope_odoo", "sin_odoo", "no_en_odoo", "bajo_minimo", "cubierto", "poca_venta"],
};

const PARAMS: { k: keyof ParametrosFull; t: string; unidad: string; titulo: string; max: number }[] = [
  { k: "cobertura_dias", t: "Cobertura", unidad: "días", max: 120,
    titulo: "Cuántos días de venta debe aguantar FULL con lo que hay, lo que va en camino y lo que se mande." },
  { k: "min_piezas", t: "Mínimo por renglón", unidad: "pzs", max: 500,
    titulo: "Si a un SKU le faltan menos piezas que esto, no se manda: no justifica un renglón." },
  { k: "min_ventas_30", t: "Venta mínima", unidad: "en 30 d", max: 500,
    titulo: "Con menos ventas FULL en 30 días el ritmo es ruido y no se propone." },
  { k: "dejar_en_bodega", t: "Dejar en bodega", unidad: "pzs/SKU", max: 5000,
    titulo: "Colchón por SKU que se queda en Odoo para los canales DROP (TikTok, Temu, Walmart). Sin regla de negocio todavía: 0." },
];

export default function CrearFull({
  cuentaInicial, stock, rol, recarga, onEstado,
}: {
  cuentaInicial: FiltroCuenta;
  stock: StockHoy | null | undefined;
  rol: Rol;
  /** Sube cuando la página pide volver a leer (botón de actualizar). */
  recarga: number;
  /** Lo que la pestaña enseña en su botón: cuántos SKUs y piezas hay por mandar. */
  onEstado?: (e: { skus: number; piezas: number; cuenta: Cuenta } | null) => void;
}) {
  const [datos, setDatos] = useState<PropuestaFull | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cargando, setCargando] = useState(true);
  const [cuenta, setCuenta] = useState<Cuenta>(cuentaInicial === "todas" ? "Kubera" : cuentaInicial);
  const [params, setParams] = useState<ParametrosFull | null>(null);
  const [editadas, setEditadas] = useState<Record<Cuenta, Record<string, number>>>({ Kubera: {}, "San Corpe": {} });
  const [filtro, setFiltro] = useState<Filtro>("mandar");
  const [busca, setBusca] = useState("");
  const [revisar, setRevisar] = useState(false);

  const cargar = useCallback(async (refrescar: boolean) => {
    setCargando(true);
    setError(null);
    try {
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full${refrescar ? "?refrescar=true" : ""}`,
                                  { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 300)}`);
      const d = await r.json() as PropuestaFull;
      setDatos(d);
      setParams((p) => p ?? d.parametros);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCargando(false);
    }
  }, []);
  useEffect(() => { void cargar(recarga > 0); }, [cargar, recarga]);
  useEffect(() => { if (cuentaInicial !== "todas") setCuenta(cuentaInicial); }, [cuentaInicial]);

  const propuestas = useMemo(
    () => (datos && params ? proponer(datos.cuentas, params) : null), [datos, params]);
  const filas = useMemo(() => propuestas?.[cuenta] ?? [], [propuestas, cuenta]);
  const cantidad = useCallback((r: Propuesta) => editadas[cuenta][r.sku] ?? r.sugerido, [editadas, cuenta]);
  const van = useMemo(() => filas.filter((r) => cantidad(r) > 0), [filas, cantidad]);
  const totalPiezas = van.reduce((a, r) => a + cantidad(r), 0);
  const cantidades = useMemo(() => Object.fromEntries(filas.map((r) => [r.sku, cantidad(r)])), [filas, cantidad]);

  useEffect(() => {
    onEstado?.(datos ? { skus: van.length, piezas: totalPiezas, cuenta } : null);
  }, [datos, van.length, totalPiezas, cuenta, onEstado]);

  const conteo = (f: Filtro) => filas.filter((r) => EN_FILTRO[f].includes(r.estado) || (f === "mandar" && cantidad(r) > 0)).length;
  const q = busca.trim().toUpperCase();
  const visibles = filas.filter((r) => (EN_FILTRO[filtro].includes(r.estado) || (filtro === "mandar" && cantidad(r) > 0))
    && (!q || r.sku.toUpperCase().includes(q) || (r.nombre ?? "").toUpperCase().includes(q)));

  const fijar = (sku: string, v: string) => {
    const n = Math.max(0, Math.min(100_000, parseInt(v.replace(/[^\d]/g, ""), 10) || 0));
    setEditadas((e) => ({ ...e, [cuenta]: { ...e[cuenta], [sku]: n } }));
  };
  const hayEdiciones = Object.keys(editadas[cuenta]).length > 0;
  const s = stock?.full[cuenta];
  const camino = datos?.en_camino?.[cuenta];
  const semana = datos?.semana;

  const descargar = () => {
    const blob = new Blob(["\uFEFF" + csvDe(filas, cantidades, cuenta)], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `full_${cuenta.replace(" ", "_").toLowerCase()}_${semana?.semana ?? "semana"}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="mt-4 flex flex-col gap-3">
      {/* ── La cuenta y su saldo ─────────────────────────────────────────── */}
      <Tarjeta>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <Ceja>
              Planeación de la semana {semana ? `${semana.semana} · ${rangoSemana(semana.lunes)}` : "…"} · hora de CDMX
            </Ceja>
            <h2 className="mt-1 text-[22px] font-extrabold tracking-tight text-slate-900">Crear FULL</h2>
            <p className="mt-0.5 max-w-2xl text-[13px] text-slate-500">
              Lo que conviene mandar a FULL según lo que vende cada SKU, lo que ya hay en FULL, lo que va en camino
              y lo libre en Odoo. Corrige lo que quieras y crea el borrador.
            </p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <div className="flex overflow-hidden rounded-xl border border-slate-200 bg-white p-0.5">
              {CUENTAS.map((c) => (
                <button key={c} type="button" onClick={() => setCuenta(c)}
                        className={`inline-flex items-center gap-2 rounded-[10px] px-4 py-2 text-sm font-bold ${
                          cuenta === c ? "bg-indigo-600 text-white shadow-sm" : "text-slate-500 hover:bg-slate-50"}`}>
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: PUNTO_CUENTA[c] }} />{c}
                </button>
              ))}
            </div>
            {datos && <EstadoInterruptor interruptor={datos.interruptor} rol={rol} onCambio={() => void cargar(false)} />}
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-2.5 lg:grid-cols-4">
          <Saldo icono={<Boxes className="h-3.5 w-3.5" />} rotulo="En FULL hoy"
                 cifra={s ? num(s.piezas) : "—"} pie={s ? `piezas en ${num(s.con_stock)} publicaciones con stock` : "leyendo…"} />
          <Saldo icono={<PackageX className="h-3.5 w-3.5" />} rotulo="Agotadas en FULL" tono="rosa"
                 cifra={s ? `${Math.round((s.en_cero / Math.max(1, s.publicaciones)) * 100)}%` : "—"}
                 pie={s ? `${num(s.en_cero)} de ${num(s.publicaciones)} publicaciones FULL en cero` : "leyendo…"} />
          <Saldo icono={<Truck className="h-3.5 w-3.5" />} rotulo="En camino a FULL" tono="ambar"
                 cifra={camino ? num(camino.piezas) : "—"}
                 pie={camino ? `piezas de ${num(camino.skus)} SKUs: por validar o sin llegar todavía` : "leyendo…"} />
          <Saldo icono={<ArrowRight className="h-3.5 w-3.5" />} rotulo="Por mandar esta semana" tono="indigo"
                 cifra={datos ? num(totalPiezas) : "—"}
                 pie={datos ? `piezas en ${num(van.length)} SKUs con la propuesta${hayEdiciones ? " y tus cambios" : ""}` : "calculando…"} />
        </div>
      </Tarjeta>

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-[12.5px] text-rose-800">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span><b>No se pudo armar la propuesta.</b> <code className="font-mono">{error}</code></span>
        </div>
      )}

      {/* ── La propuesta ─────────────────────────────────────────────────── */}
      <Tarjeta>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div className="flex flex-wrap items-end gap-2.5">
            {params && PARAMS.map((p) => (
              <label key={p.k} title={p.titulo} className="flex flex-col gap-1">
                <span className="text-[10.5px] font-bold uppercase tracking-[.06em] text-slate-400">{p.t}</span>
                <span className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2 py-1">
                  <input type="text" inputMode="numeric" value={params[p.k]}
                         onChange={(ev) => {
                           const n = Math.max(0, Math.min(p.max, parseInt(ev.target.value.replace(/[^\d]/g, ""), 10) || 0));
                           setParams({ ...params, [p.k]: n });
                         }}
                         className="w-12 bg-transparent text-right font-mono text-sm font-bold tabular-nums text-slate-900 focus:outline-none" />
                  <span className="text-[11px] text-slate-400">{p.unidad}</span>
                </span>
              </label>
            ))}
            {datos && params && (JSON.stringify(params) !== JSON.stringify(datos.parametros) || hayEdiciones) && (
              <button type="button"
                      onClick={() => { setParams(datos.parametros); setEditadas((e) => ({ ...e, [cuenta]: {} })); }}
                      className="mb-0.5 inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-semibold text-slate-500 hover:bg-slate-100">
                <RotateCcw className="h-3.5 w-3.5" /> volver a la propuesta
              </button>
            )}
          </div>
          <p className="max-w-sm text-[11px] leading-snug text-slate-400">
            Supuestos sin dueño todavía: cámbialos y la propuesta se recalcula al instante. Nada se guarda hasta crear.
          </p>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          {FILTROS.map((f) => (
            <button key={f.k} type="button" title={f.titulo} onClick={() => setFiltro(f.k)}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-bold ${
                      filtro === f.k ? "border-indigo-200 bg-indigo-50 text-indigo-800" : "border-slate-200 bg-white text-slate-500"}`}>
              {f.t}<span className="font-mono text-[11px] opacity-70">{conteo(f.k)}</span>
            </button>
          ))}
          <label className="ml-auto flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5">
            <Search className="h-3.5 w-3.5 text-slate-400" />
            <input value={busca} onChange={(ev) => setBusca(ev.target.value)} placeholder="Buscar SKU o producto"
                   className="w-44 bg-transparent text-xs text-slate-700 placeholder:text-slate-400 focus:outline-none" />
          </label>
        </div>

        <div className="mt-3 overflow-x-auto rounded-xl border border-slate-200">
          <table className="w-full min-w-[1080px] border-collapse text-[12.5px]">
            <thead>
              <tr className="bg-slate-50 text-left text-[10px] font-bold uppercase tracking-[.06em] text-slate-500">
                <th className="px-3 py-2.5">SKU · producto</th>
                <th className="px-3 py-2.5 text-right" title="Ventas FULL de los últimos 30 días completos, y su ritmo diario.">Vende 30 d</th>
                <th className="px-3 py-2.5 text-right" title="Piezas en el almacén de ML hoy (channel.listings).">En FULL</th>
                <th className="px-3 py-2.5 text-right" title="Cuántos días aguanta lo que hay en FULL al ritmo de 30 días.">Aguanta</th>
                <th className="px-3 py-2.5 text-right" title="Salidas por validar o validadas que ML todavía no avisa, más borradores FULL en Odoo.">En camino</th>
                <th className="px-3 py-2.5 text-right" title="Libre en Odoo (free_qty) por almacén: lo que se puede prometer.">Libre en Odoo</th>
                <th className="px-3 py-2.5 text-right">Sugerido</th>
                <th className="px-3 py-2.5 text-right">A mandar</th>
              </tr>
            </thead>
            <tbody>
              {visibles.map((r) => (
                <FilaPropuestaUI key={r.sku} r={r} valor={cantidad(r)} editada={r.sku in editadas[cuenta]}
                                 onCambio={(v) => fijar(r.sku, v)} />
              ))}
              {!datos && (
                <tr><td colSpan={8} className="px-4 py-12 text-center text-sm text-slate-500" style={{ background: FONDO_RAYADO }}>
                  {cargando ? "Leyendo ventas FULL, stock en FULL, envíos en camino y lo libre en Odoo…" : "Sin propuesta: revisa el error de arriba."}
                </td></tr>
              )}
              {datos && visibles.length === 0 && (
                <tr><td colSpan={8} className="px-4 py-10 text-center text-sm text-slate-500">
                  Nada en este filtro para {cuenta}.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        {/* ── La barra de acción ───────────────────────────────────────── */}
        <div className="sticky bottom-3 z-10 mt-3 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-indigo-200 bg-indigo-50/95 px-4 py-3 shadow-sm backdrop-blur">
          <div className="flex items-center gap-3">
            <ChipCuenta cuenta={cuenta} />
            <span className="text-sm text-indigo-900">
              <b className="font-mono tabular-nums">{num(van.length)}</b> SKUs ·{" "}
              <b className="font-mono tabular-nums">{num(totalPiezas)}</b> piezas
              {semana ? <span className="text-indigo-700/70"> · semana {semana.semana}</span> : null}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button type="button" onClick={descargar} disabled={!van.length}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-sm font-semibold text-indigo-700 hover:bg-indigo-50 disabled:opacity-40">
              <Download className="h-4 w-4" /> Descargar lista
            </button>
            <button type="button" onClick={() => setRevisar(true)} disabled={!van.length}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-bold text-white shadow-sm hover:bg-indigo-700 disabled:opacity-40">
              Revisar y crear FULL <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </Tarjeta>

      {datos && <Pendientes datos={datos} cuenta={cuenta} />}

      <p className="text-xs leading-relaxed text-slate-400">
        {datos ? `Fuente: ${datos.fuente}. Leído ${new Date(datos.generado).toLocaleString("es-MX", { timeZone: "America/Mexico_City" })}.` : ""}
      </p>

      {revisar && params && (
        <ConfirmarFull cuenta={cuenta} semana={semana?.semana ?? ""} params={params} rol={rol}
                       lineas={van.map((r) => ({ sku: r.sku, cantidad: cantidad(r), sugerido: r.sugerido }))}
                       onDescargar={descargar}
                       onCerrar={() => setRevisar(false)}
                       onCreado={() => { setEditadas((e) => ({ ...e, [cuenta]: {} })); void cargar(true); }} />
      )}
    </div>
  );
}

function Saldo({ icono, rotulo, cifra, pie, tono = "slate" }: {
  icono: ReactNode; rotulo: string; cifra: string; pie: string; tono?: "slate" | "rosa" | "ambar" | "indigo";
}) {
  const c = {
    slate: "border-slate-200 bg-white text-slate-900",
    rosa: "border-rose-200 bg-rose-50 text-rose-800",
    ambar: "border-amber-200 bg-amber-50 text-amber-800",
    indigo: "border-indigo-200 bg-indigo-50 text-indigo-900",
  }[tono];
  return (
    <div className={`rounded-xl border px-3.5 py-3 ${c}`}>
      <div className="flex items-center gap-1.5 text-[10.5px] font-bold uppercase tracking-[.06em] opacity-70">{icono}{rotulo}</div>
      <div className="mt-1 text-2xl font-extrabold leading-none tracking-tight tabular-nums">{cifra}</div>
      <div className="mt-1 text-[11.5px] opacity-80">{pie}</div>
    </div>
  );
}

const NOTA: Record<EstadoPropuesta, (r: Propuesta) => { texto: string; clase: string }> = {
  mandar: () => ({ texto: "", clase: "" }),
  tope_odoo: (r) => ({ texto: `Odoo sólo tiene ${num(r.libre_asignado)} de ${num(r.necesidad)}`, clase: "text-amber-700" }),
  sin_odoo: (r) => ({ texto: `le faltan ${num(r.necesidad)} y Odoo no tiene libre`, clase: "text-rose-700" }),
  no_en_odoo: () => ({ texto: "el SKU no está en Odoo", clase: "text-rose-700" }),
  bajo_minimo: (r) => ({ texto: `le faltan ${num(r.necesidad)}: menos del mínimo`, clase: "text-slate-400" }),
  cubierto: () => ({ texto: "cubierto", clase: "text-emerald-700" }),
  poca_venta: () => ({ texto: "vende poco", clase: "text-slate-400" }),
};

function FilaPropuestaUI({ r, valor, editada, onCambio }: {
  r: Propuesta; valor: number; editada: boolean; onCambio: (v: string) => void;
}) {
  const nota = NOTA[r.estado](r);
  const agotada = r.stock_full === 0;
  const tonoAguanta = r.aguanta === null ? "text-slate-300"
    : r.aguanta < 7 ? "text-rose-700" : r.aguanta < 15 ? "text-amber-700" : "text-slate-600";
  const libre = r.libre ? Object.entries(r.libre) : null;
  return (
    <tr className={`border-t border-slate-100 align-top ${valor > 0 ? "" : "bg-slate-50/40"}`}>
      <td className="px-3 py-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-[12.5px] font-bold text-slate-900">{r.sku}</span>
          {agotada && <span className="rounded border border-rose-200 bg-rose-50 px-1.5 text-[9.5px] font-bold uppercase text-rose-700">agotada</span>}
          {r.situacion === "under_review" && <span className="rounded border border-amber-300 bg-amber-50 px-1.5 text-[9.5px] font-bold uppercase text-amber-700">en revisión</span>}
        </div>
        <div className="max-w-[340px] truncate text-[11px] text-slate-400" title={r.nombre ?? ""}>{r.nombre ?? "—"}</div>
        {r.listing_id && (
          <a href={r.url ?? `https://articulo.mercadolibre.com.mx/${r.listing_id.replace("MLM", "MLM-")}`} target="_blank"
             rel="noreferrer" className="inline-flex items-center gap-0.5 font-mono text-[10.5px] text-indigo-500 hover:underline">
            {r.listing_id}<ExternalLink className="h-2.5 w-2.5" />
          </a>
        )}
      </td>
      <td className="px-3 py-2 text-right">
        <div className="font-mono font-bold tabular-nums text-slate-800">{num(r.v30)}</div>
        <div className="text-[10.5px] text-slate-400"
             title={`Última semana: ${r.v7} piezas (${(r.v7 / 7).toFixed(1)}/día)`}>
          {r.venta_dia.toFixed(1)}/día{r.sube ? <span className="font-bold text-emerald-600"> ↑</span> : null}
        </div>
      </td>
      <td className="px-3 py-2 text-right font-mono tabular-nums">
        {r.stock_full === null
          ? <span title="La publicación FULL no está en channel.listings: no se sabe cuánto hay (no es 0)."
                  className="rounded px-1.5 text-slate-400" style={{ background: FONDO_RAYADO }}>?</span>
          : <span className={agotada ? "font-bold text-rose-700" : "text-slate-700"}>{num(r.stock_full)}</span>}
      </td>
      <td className={`px-3 py-2 text-right font-mono text-[12px] tabular-nums ${tonoAguanta}`}>
        {r.aguanta === null ? "—" : r.aguanta >= 99 ? "99+ d" : `${Math.floor(r.aguanta)} d`}
      </td>
      <td className="px-3 py-2 text-right">
        {r.en_camino > 0
          ? <div className="font-mono tabular-nums text-amber-700" title={r.camino.join("\n")}>{num(r.en_camino)}</div>
          : <div className="font-mono text-slate-300">0</div>}
        {r.borrador > 0 && (
          <div className="text-[10.5px] text-indigo-600" title={`Borradores FULL en Odoo: ${r.borradores.join(", ")}`}>
            +{num(r.borrador)} en borrador
          </div>
        )}
      </td>
      <td className="px-3 py-2 text-right text-[11.5px]">
        {libre ? libre.map(([alm, n]) => (
          <div key={alm} className={`font-mono tabular-nums ${n > 0 ? "text-slate-700" : "text-slate-300"}`}>
            <span className="text-[10px] text-slate-400">{alm}</span> {num(n)}
          </div>
        )) : <span className="text-rose-700">no está en Odoo</span>}
      </td>
      <td className="px-3 py-2 text-right">
        <div className="font-mono font-bold tabular-nums text-indigo-700">{r.sugerido ? num(r.sugerido) : "—"}</div>
        {nota.texto && <div className={`text-[10.5px] ${nota.clase}`}>{nota.texto}</div>}
        {r.repartido && <div className="text-[10.5px] text-violet-700" title={r.repartido}>repartido con la otra cuenta</div>}
      </td>
      <td className="px-3 py-2 text-right">
        <input type="text" inputMode="numeric" value={valor ? String(valor) : ""} placeholder="0"
               onChange={(ev) => onCambio(ev.target.value)}
               className={`w-20 rounded-md border px-2 py-1 text-right font-mono text-[13px] font-bold tabular-nums focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 ${
                 editada ? "border-indigo-300 bg-indigo-50 text-indigo-900" : "border-slate-200 text-slate-900"}`} />
      </td>
    </tr>
  );
}

/** Lo que ya existe en Odoo y conviene tener a la vista antes de crear otro. */
function Pendientes({ datos, cuenta }: { datos: PropuestaFull; cuenta: Cuenta }) {
  const borr = datos.borradores.filter((b) => b.cuenta === cuenta || b.cuenta === null);
  const zombis = datos.zombis.filter((z) => z.cuenta === cuenta);
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <Tarjeta>
        <Ceja>Borradores FULL en Odoo · últimos 21 días</Ceja>
        <p className="mt-1 text-[12px] text-slate-500">
          Cotizaciones que todavía nadie confirma. Las de {cuenta} ya se restan de la propuesta; las «sin cuenta» no,
          porque no se sabe de cuál son.
        </p>
        <div className="mt-2 divide-y divide-slate-100 rounded-xl border border-slate-200">
          {borr.map((b) => (
            <a key={b.id} href={b.url} target="_blank" rel="noreferrer"
               className="flex flex-wrap items-baseline justify-between gap-2 px-3 py-2 text-[12.5px] hover:bg-slate-50">
              <span>
                <span className="font-mono font-bold text-slate-800">{b.orden}</span>
                <span className="text-slate-400"> · {b.kam ?? "—"} · {dia(b.creada)}{b.almacen ? ` · ${b.almacen}` : ""}</span>
                {b.referencia && <span className="block text-[11px] text-slate-400">ref «{b.referencia}»</span>}
              </span>
              <span className={`text-[11.5px] font-semibold ${b.cuenta ? "text-slate-600" : "text-amber-700"}`}
                    title={b.cuenta_regla}>
                {num(b.piezas)} pzs · {b.skus} SKUs · {b.cuenta ?? "sin cuenta"}
              </span>
            </a>
          ))}
          {borr.length === 0 && <p className="px-3 py-3 text-[12px] text-slate-400">Ninguno.</p>}
        </div>
      </Tarjeta>
      <Tarjeta>
        <Ceja>Órdenes FULL abiertas hace más de 21 días</Ceja>
        <p className="mt-1 text-[12px] text-slate-500">
          No se cuentan como «en camino»: son órdenes que nadie cerró y siguen reservando stock en Odoo.
          Conviene cerrarlas o cancelarlas.
        </p>
        <div className="mt-2 divide-y divide-slate-100 rounded-xl border border-slate-200">
          {zombis.map((z) => (
            <div key={`${z.orden}-${z.salida}`} className="flex items-baseline justify-between px-3 py-2 text-[12.5px]">
              <span className="font-mono font-bold text-slate-800">{z.orden}
                <span className="font-normal text-slate-400"> · {z.salida} · abierta desde el {dia(z.creada)}</span>
              </span>
              <span className="text-[11.5px] font-semibold text-amber-700">{num(z.piezas)} pzs</span>
            </div>
          ))}
          {zombis.length === 0 && <p className="px-3 py-3 text-[12px] text-slate-400">Ninguna en {cuenta}.</p>}
        </div>
      </Tarjeta>
    </div>
  );
}

/** El interruptor que deja a «Crear FULL» escribir en Odoo. Sólo lo mueve un admin. */
function EstadoInterruptor({ interruptor, rol, onCambio }: {
  interruptor: Interruptor; rol: Rol; onCambio: () => void;
}) {
  const [preguntar, setPreguntar] = useState(false);
  const [moviendo, setMoviendo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const encendido = interruptor.encendido;
  const mover = async (valor: boolean) => {
    setMoviendo(true);
    setError(null);
    try {
      const motivo = encodeURIComponent(valor ? "encendido desde Crear FULL" : "apagado desde Crear FULL");
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full/interruptor?encendido=${valor}&motivo=${motivo}`,
                                  { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || d.ok === false) throw new Error(d.motivo ?? d.detail ?? `HTTP ${r.status}`);
      setPreguntar(false);
      onCambio();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setMoviendo(false);
    }
  };
  return (
    <div className="flex max-w-[380px] flex-col items-end gap-1.5">
      <div className="flex items-center gap-2">
        <span title={interruptor.actualizado_por ? `Lo movió ${interruptor.actualizado_por} el ${dia(interruptor.actualizado_at)}` : "Nadie lo ha movido: vale la configuración por omisión (apagado)."}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-bold ${
                encendido ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-slate-200 bg-slate-50 text-slate-500"}`}>
          <Power className="h-3 w-3" /> Crear en Odoo: {encendido ? "encendido" : "apagado"}
        </span>
        {rol === "admin" && !preguntar && (
          <button type="button" onClick={() => (encendido ? void mover(false) : setPreguntar(true))} disabled={moviendo}
                  className="text-[11px] font-semibold text-indigo-600 hover:underline disabled:opacity-50">
            {encendido ? "apagar" : "encender"}
          </button>
        )}
      </div>
      {preguntar && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2.5 text-[11.5px] leading-relaxed text-amber-900">
          Al encender, «Crear FULL» escribe en Odoo: una <b>cotización en borrador</b> por almacén, con el socio
          fijo de la cuenta (FULL KUBERA / FULL SAN CORPE), precio 0 y sin impuestos. <b>No confirma ni reserva
          stock</b>: eso lo sigue haciendo la KAM en Odoo.
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" onClick={() => setPreguntar(false)} className="rounded-md px-2 py-1 font-semibold text-amber-800 hover:bg-amber-100">
              Cancelar
            </button>
            <button type="button" onClick={() => void mover(true)} disabled={moviendo}
                    className="rounded-md bg-amber-600 px-2.5 py-1 font-bold text-white hover:bg-amber-700 disabled:opacity-50">
              Sí, encender
            </button>
          </div>
        </div>
      )}
      {error && <span className="text-[11px] text-rose-700">No se pudo: {error}</span>}
    </div>
  );
}

/** Los estados de `sale.order` como los dice una persona. */
const ESTADO_ODOO: Record<string, string> = {
  draft: "borrador", sent: "cotización enviada", sale: "confirmada", done: "bloqueada", cancel: "cancelada",
};

/**
 * La confirmación: la vista previa EXACTA de lo que se crearía (con el libre de
 * Odoo releído ahora), y el botón que la escribe cuando el interruptor lo deja.
 */
function ConfirmarFull({ cuenta, semana, params, rol, lineas, onDescargar, onCerrar, onCreado }: {
  cuenta: Cuenta; semana: string; params: ParametrosFull; rol: Rol;
  lineas: { sku: string; cantidad: number; sugerido: number }[];
  onDescargar: () => void; onCerrar: () => void; onCreado: () => void;
}) {
  // La clave nace UNA vez por confirmación: reintentar con ella no duplica.
  const [clave] = useState(() => (typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID() : `${Date.now()}${Math.random()}`).replace(/[^0-9a-z]/gi, "").slice(0, 12));
  const [previa, setPrevia] = useState<ResultadoCrear | null>(null);
  const [resultado, setResultado] = useState<ResultadoCrear | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creando, setCreando] = useState(false);
  const cuerpo = JSON.stringify({ cuenta, lineas, clave, parametros: params });

  useEffect(() => {
    let vivo = true;
    // El Content-Type va en `extra`: `fetchSesion` arma las cabeceras de cero y
    // las de `init` se pierden (el cuerpo llegaba como texto y FastAPI daba 422).
    fetchSesion(`${API_BASE}/api/fulfillment/crear-full/vista-previa`,
                { method: "POST", body: cuerpo }, { "Content-Type": "application/json" })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
        return r.json() as Promise<ResultadoCrear>;
      })
      .then((d) => { if (vivo) setPrevia(d); })
      .catch((e: unknown) => { if (vivo) setError(e instanceof Error ? e.message : String(e)); });
    return () => { vivo = false; };
    // La vista previa es de ESTA confirmación: no se repite al re-renderizar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const crear = async () => {
    setCreando(true);
    setError(null);
    try {
      const r = await fetchSesion(`${API_BASE}/api/fulfillment/crear-full`,
                                  { method: "POST", body: cuerpo }, { "Content-Type": "application/json" });
      const d = await r.json().catch(() => ({})) as ResultadoCrear & { detail?: string };
      if (!r.ok) throw new Error(d.detail ?? `HTTP ${r.status}`);
      setResultado(d);
      if (d.accion === "creada") onCreado();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreando(false);
    }
  };

  const v = resultado ?? previa;
  const encendido = !!previa?.interruptor?.encendido;
  const puede = encendido && rol === "admin" && !!previa?.ok && !resultado;
  const porQueNo = !previa ? "" : !previa.ok ? (previa.motivo ?? "No hay nada que crear.")
    : rol !== "admin" ? "Crear en Odoo es de admin. Puedes descargar la lista o pedir que la creen."
    : !encendido ? "La creación en Odoo está apagada: esto es exactamente lo que se crearía. Enciéndela arriba, en «Crear en Odoo»."
    : "";

  return (
    <Ventana etiqueta={`Crear FULL ${cuenta}`} onCerrar={onCerrar} ancho="max-w-[860px]">
      <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-6 py-4">
        <div>
          <Ceja>Crear FULL · semana {semana}</Ceja>
          <h3 className="mt-1 flex items-center gap-2 text-lg font-extrabold text-slate-900">
            FULL {cuenta} <ChipCuenta cuenta={cuenta} />
          </h3>
          <p className="mt-0.5 text-[12.5px] text-slate-500">
            {v ? <>{num(v.piezas ?? 0)} piezas de {num(v.piezas_pedidas ?? 0)} pedidas · socio <b>{v.socio}</b> · borrador, precio 0, sin impuestos</>
               : "Releyendo lo libre en Odoo…"}
          </p>
        </div>
        <BotonCerrar onClick={onCerrar} />
      </div>

      <div className="px-6 py-4">
        {error && (
          <p className="mb-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-[12.5px] text-rose-800">
            {error}
          </p>
        )}
        {resultado?.ordenes && (
          <div className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-[13px] text-emerald-900">
            <div className="flex items-center gap-2 font-bold">
              <CheckCircle2 className="h-4 w-4" />
              {resultado.accion === "ya_existia" ? "Ya estaba creada: no se duplicó." : "Creada en Odoo, en borrador."}
            </div>
            <ul className="mt-1.5 flex flex-col gap-1">
              {resultado.ordenes.map((o) => (
                <li key={o.id}>
                  <a href={o.url} target="_blank" rel="noreferrer" className="font-mono font-bold underline">{o.orden}</a>
                  {" "}· {o.almacen} · {num(o.piezas)} pzs · {ESTADO_ODOO[o.estado] ?? o.estado}
                </li>
              ))}
            </ul>
            <p className="mt-1.5 text-[12px]">
              Falta: que la KAM la confirme en Odoo y teclee el número del envío de ML en la referencia.
              {resultado.solicitud_guardada === false && " La solicitud original NO se guardó (falta la tabla ops.fulfillment_solicitudes, migración 0054)."}
            </p>
          </div>
        )}

        {!v && !error && <p className="py-8 text-center text-sm text-slate-400">Armando la vista previa…</p>}

        {v?.partes?.map((p) => (
          <div key={p.almacen_id} className="mb-3">
            <div className="flex items-baseline justify-between">
              <span className="text-[13px] font-bold text-slate-800">Cotización desde {p.almacen}</span>
              <span className="text-[12px] text-slate-500">{p.lineas.length} SKUs · {num(p.piezas)} pzs</span>
            </div>
            <div className="mt-1.5 max-h-[260px] overflow-y-auto rounded-xl border border-slate-200">
              <table className="w-full text-[12px]">
                <tbody>
                  {p.lineas.map((l) => (
                    <tr key={l.sku} className="border-t border-slate-100 first:border-t-0">
                      <td className="px-3 py-1.5 font-mono font-bold text-slate-800">{l.sku}</td>
                      <td className="max-w-[420px] truncate px-3 py-1.5 text-slate-500">{l.nombre}</td>
                      <td className="px-3 py-1.5 text-right font-mono font-bold tabular-nums text-slate-900">{num(l.cantidad)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}

        {!!v?.recortes?.length && (
          <div className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-[12px] text-amber-900">
            <b>Odoo no tiene todo lo pedido</b> (lo libre se releyó ahora):
            <ul className="mt-1 list-inside list-disc">
              {v.recortes.map((x) => (
                <li key={x.sku}><span className="font-mono">{x.sku}</span>: pediste {num(x.pedidas)}, van {num(x.van)} — {x.porque}</li>
              ))}
            </ul>
          </div>
        )}
        {!!v?.no_en_odoo?.length && (
          <p className="mb-3 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-[12px] text-rose-800">
            No están en Odoo y no se incluyen: <span className="font-mono">{v.no_en_odoo.join(", ")}</span>
          </p>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-b-2xl border-t border-slate-100 bg-slate-50/60 px-6 py-3">
        <p className="max-w-[460px] text-[11.5px] text-slate-500">
          {porQueNo || "Se crea en BORRADOR, una cotización por almacén. No confirma ni reserva stock."}
        </p>
        <div className="flex items-center gap-2">
          <button type="button" onClick={onDescargar}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50">
            <Download className="h-4 w-4" /> Descargar lista
          </button>
          {resultado ? (
            <button type="button" onClick={onCerrar}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-bold text-white hover:bg-indigo-700">
              Cerrar
            </button>
          ) : (
            <button type="button" onClick={() => void crear()} disabled={!puede || creando}
                    title={porQueNo || undefined}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-bold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-indigo-300">
              {creando ? "Creando…" : "Crear borrador en Odoo"}
            </button>
          )}
        </div>
      </div>
    </Ventana>
  );
}
