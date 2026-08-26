"use client";

/**
 * /analisis/metricas — KPIs de publicaciones de Mercado Libre (Bekura y San
 * Corpe), por semana ISO 8601 (lunes-domingo) con comparativo vs la semana
 * anterior.
 *
 * Catálogo de KPIs en vez de tarjetas fijas (Jose, 24-ago): hoy solo hay 4,
 * pero se van a ir sumando con el tiempo. Arriba, "KPIs principales" muestra
 * como máximo 6 — los que estén fijados (localStorage, por navegador). Abajo,
 * "Todos los KPIs" es el catálogo completo con un botón para fijar/desfijar
 * cada uno. Agregar el próximo KPI es sumar una entrada a `KPIS`, no
 * reestructurar la página.
 *
 * Activaciones = `date_published` (fecha real de ML, migración 0031) dentro
 * del rango — no transiciones de `situacion`. Ticket promedio y Visitas
 * bajas son SNAPSHOT de hoy (listings activos), no reconstrucciones
 * históricas dentro de la semana — decisiones validadas con Jose antes de
 * construir esto. Visitas bajas en particular NO dispara mediciones nuevas:
 * usa lo que ya está capturado en `enrich.market_listing_metrics`
 * (Competencia), así que `sin_medir` puede ser una porción real del
 * catálogo activo.
 *
 * "Publicaciones pausadas" existió y se quitó (Jose, 26-ago): no la quería
 * en ninguna tienda. El backend ya no la calcula.
 */

import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Loader2, Pin, PinOff } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";

interface ItemActivacion {
  sku: string; cuenta: string; listing_id: string | null;
  date_published: string; situacion: string | null; titulo: string | null;
}
interface Resp {
  periodo: { desde: string; hasta: string; semana_iso: number; anio_iso: number };
  activaciones: {
    total: number; por_cuenta: Record<string, number>;
    delta_pct: number | null; items: ItemActivacion[];
  };
  ticket_promedio: {
    consolidado: number | null; por_cuenta: Record<string, number>; snapshot_at: string;
  };
  visitas_bajas: {
    total: number; medidas: number; sin_medir: number; bajas: number; pct: number | null;
    por_cuenta: Record<string, { total: number; medidas: number; bajas: number; pct: number | null }>;
    snapshot_at: string;
  };
}

const n = (v: number | string | null | undefined) => (v == null ? 0 : Number(v));
const fMoney = (v: number | string | null | undefined) =>
  v == null ? "—" : `$${n(v).toLocaleString("es-MX", { maximumFractionDigits: 2 })}`;
const fNum = (v: number | string | null | undefined) =>
  v == null ? "—" : n(v).toLocaleString("es-MX");
const fFecha = (iso: string) =>
  new Date(iso).toLocaleDateString("es-MX", { day: "numeric", month: "short" });
const fFechaHora = (iso: string) =>
  new Date(iso).toLocaleString("es-MX", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

const CUENTAS = [
  { id: "", label: "Consolidado" },
  { id: "BEKURA", label: "Bekura" },
  { id: "SANCORFASHION", label: "Sancor" },
];

const LS_KEY = "metricas_kpis_fijados";
const MAX_FIJADOS = 6;

function Kpi({ label, value, pie, tone }: {
  label: string; value: string; pie?: React.ReactNode; tone?: string;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">{label}</div>
      <div className={`mt-1 text-2xl font-bold ${tone ?? "text-slate-900"}`}>{value}</div>
      {pie && <div className="mt-0.5 text-[11px] text-slate-400">{pie}</div>}
    </div>
  );
}

function Delta({ pct }: { pct: number | null }) {
  if (pct == null) return <span className="text-slate-400">s/ base la semana pasada</span>;
  const arriba = pct > 0;
  const plano = pct === 0;
  return (
    <span className={plano ? "text-slate-400" : arriba ? "text-emerald-600" : "text-rose-600"}>
      {plano ? "sin cambio" : `${arriba ? "+" : ""}${pct}%`} vs semana pasada
    </span>
  );
}

type KpiDef = {
  id: string;
  titulo: string;
  grupo: string;
  calcular: (d: Resp, cuenta: string) => { value: string; pie?: string; tone?: string };
};

const KPIS: KpiDef[] = [
  {
    id: "activaciones", titulo: "Publicaciones activadas", grupo: "Publicaciones",
    calcular: (d, cuenta) => ({
      value: fNum(cuenta ? d.activaciones.por_cuenta[cuenta] ?? 0 : d.activaciones.total),
      tone: "text-indigo-600",
    }),
  },
  {
    id: "ticket_promedio", titulo: "Ticket promedio (activas)", grupo: "Precio",
    calcular: (d, cuenta) => ({
      value: fMoney(cuenta ? d.ticket_promedio.por_cuenta[cuenta] : d.ticket_promedio.consolidado),
      pie: `snapshot de hoy · ${fFechaHora(d.ticket_promedio.snapshot_at)}`,
      tone: "text-emerald-600",
    }),
  },
  {
    id: "visitas_bajas", titulo: "Activas con visitas bajas (0-100)", grupo: "Visitas",
    calcular: (d, cuenta) => {
      const v = cuenta ? d.visitas_bajas.por_cuenta[cuenta] : undefined;
      const bajas = v ? v.bajas : d.visitas_bajas.bajas;
      const total = v ? v.total : d.visitas_bajas.total;
      const pct = v ? v.pct : d.visitas_bajas.pct;
      return {
        value: fNum(bajas),
        pie: `${pct ?? "—"}% de ${fNum(total)} activas · snapshot de hoy`,
        tone: "text-rose-600",
      };
    },
  },
];

function usePines() {
  const [fijados, setFijados] = useState<string[]>([]);
  useEffect(() => {
    try {
      const guardado = JSON.parse(localStorage.getItem(LS_KEY) || "null");
      setFijados(Array.isArray(guardado) ? guardado : KPIS.slice(0, MAX_FIJADOS).map((k) => k.id));
    } catch {
      setFijados(KPIS.slice(0, MAX_FIJADOS).map((k) => k.id));
    }
  }, []);
  const alternar = (id: string) => {
    setFijados((prev) => {
      const next = prev.includes(id)
        ? prev.filter((x) => x !== id)
        : prev.length >= MAX_FIJADOS ? prev : [...prev, id];
      try { localStorage.setItem(LS_KEY, JSON.stringify(next)); } catch { /* privado/bloqueado: se pierde al recargar */ }
      return next;
    });
  };
  return { fijados, alternar };
}

export default function MetricasPage() {
  const [cuenta, setCuenta] = useState("");
  const [rangoLibre, setRangoLibre] = useState(false);
  const [semanaOffset, setSemanaOffset] = useState(0); // 0 = semana actual, -1 = anterior…
  const [desde, setDesde] = useState("");
  const [hasta, setHasta] = useState("");
  const [datos, setDatos] = useState<Resp | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { fijados, alternar } = usePines();
  const [kpiActivo, setKpiActivo] = useState<string | null>(null);

  useEffect(() => {
    let vivo = true;
    setCargando(true);
    setError(null);
    const q = new URLSearchParams();
    if (cuenta) q.set("cuenta", cuenta);
    if (rangoLibre && desde) q.set("desde", desde);
    if (rangoLibre && hasta) q.set("hasta", hasta);
    fetchSesion(`${API_BASE}/api/analisis/metricas?${q.toString()}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : r.json().then((e) => Promise.reject(e.detail ?? r.status))))
      .then((d: Resp) => { if (vivo) setDatos(d); })
      .catch((e) => { if (vivo) setError(String(e)); })
      .finally(() => { if (vivo) setCargando(false); });
    return () => { vivo = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cuenta, rangoLibre, desde, hasta, semanaOffset]);

  // El stepper de semana recalcula desde/hasta en cliente (lunes-domingo,
  // ±7 días por click) y los manda como si fueran rango libre — así el
  // backend no necesita saber de "offsets", solo de fechas.
  const moverSemana = (delta: number) => {
    const base = datos ? new Date(`${datos.periodo.desde}T00:00:00`) : new Date();
    base.setDate(base.getDate() + delta * 7);
    const lunes = new Date(base);
    const dow = (lunes.getDay() + 6) % 7; // 0=lunes
    lunes.setDate(lunes.getDate() - dow);
    const domingo = new Date(lunes);
    domingo.setDate(domingo.getDate() + 6);
    const iso = (d: Date) => d.toISOString().slice(0, 10);
    setRangoLibre(true);
    setDesde(iso(lunes));
    setHasta(iso(domingo));
    setSemanaOffset((o) => o + delta);
  };

  const volverAHoy = () => {
    setRangoLibre(false);
    setDesde(""); setHasta("");
    setSemanaOffset(0);
  };

  const principales = useMemo(
    () => KPIS.filter((k) => fijados.includes(k.id)).slice(0, MAX_FIJADOS),
    [fijados],
  );
  const porGrupo = useMemo(() => {
    const g = new Map<string, KpiDef[]>();
    for (const k of KPIS) g.set(k.grupo, [...(g.get(k.grupo) ?? []), k]);
    return g;
  }, []);

  const tablaActiva = kpiActivo && fijados.includes(kpiActivo) ? kpiActivo : null;

  return (
    <div className="space-y-4">
      {/* Controles */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white p-1 shadow-sm">
          {CUENTAS.map((c) => (
            <button key={c.id} onClick={() => setCuenta(c.id)}
                    className={`rounded-lg px-3 py-1.5 text-sm transition-colors ${
                      cuenta === c.id
                        ? "bg-indigo-600 font-semibold text-white"
                        : "font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-800"}`}>
              {c.label}
            </button>
          ))}
        </div>

        {!rangoLibre || semanaOffset !== 0 ? (
          <div className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white px-1.5 py-1 shadow-sm">
            <button onClick={() => moverSemana(-1)} className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-100"
                    title="Semana anterior">
              <ChevronLeft size={16} />
            </button>
            <span className="min-w-[190px] px-1 text-center text-sm font-medium text-slate-700">
              {datos ? (
                <>
                  Semana {datos.periodo.semana_iso}
                  <span className="ml-1 text-slate-400">
                    · {fFecha(datos.periodo.desde)} – {fFecha(datos.periodo.hasta)}
                  </span>
                </>
              ) : "Semana…"}
            </span>
            <button onClick={() => moverSemana(1)} className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-100"
                    title="Semana siguiente">
              <ChevronRight size={16} />
            </button>
            {semanaOffset !== 0 && (
              <button onClick={volverAHoy}
                      className="ml-1 rounded-lg px-2 py-1 text-[11px] font-medium text-indigo-600 hover:bg-indigo-50">
                Hoy
              </button>
            )}
          </div>
        ) : null}

        <label className="flex items-center gap-1.5 text-sm text-slate-600">
          <input type="checkbox" checked={rangoLibre && semanaOffset === 0 && !!desde}
                 onChange={(e) => {
                   if (e.target.checked) {
                     setRangoLibre(true); setSemanaOffset(0);
                     if (datos) { setDesde(datos.periodo.desde); setHasta(datos.periodo.hasta); }
                   } else {
                     volverAHoy();
                   }
                 }}
                 className="rounded border-slate-300" />
          rango personalizado
        </label>
        {rangoLibre && semanaOffset === 0 && (
          <div className="flex items-center gap-1.5">
            <input type="date" value={desde} max={hasta || undefined}
                   onChange={(e) => setDesde(e.target.value)}
                   className="rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
            <span className="text-slate-400">–</span>
            <input type="date" value={hasta} min={desde || undefined}
                   onChange={(e) => setHasta(e.target.value)}
                   className="rounded-lg border border-slate-200 px-2 py-1.5 text-sm" />
          </div>
        )}
      </div>

      {error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          No se pudieron leer las métricas: {error}
        </div>
      )}

      {cargando && !datos && (
        <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-8 text-sm text-slate-500 shadow-sm">
          <Loader2 size={16} className="animate-spin" /> Leyendo publicaciones…
        </div>
      )}

      {datos && (
        <>
          <div>
            <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              KPIs principales
            </div>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
              {principales.map((k) => {
                const r = k.calcular(datos, cuenta);
                const activo = kpiActivo === k.id;
                const clicable = k.id === "activaciones";
                return (
                  <button key={k.id} type="button"
                          onClick={() => clicable && setKpiActivo(activo ? null : k.id)}
                          className={`text-left ${clicable ? "cursor-pointer" : "cursor-default"} ${
                            activo ? "ring-2 ring-indigo-400 rounded-xl" : ""}`}>
                    <Kpi label={k.titulo} value={r.value}
                         pie={r.pie ?? (
                           k.id === "activaciones" ? <Delta pct={datos.activaciones.delta_pct} />
                           : undefined
                         )}
                         tone={r.tone} />
                  </button>
                );
              })}
              {principales.length === 0 && (
                <div className="col-span-full rounded-xl border border-dashed border-slate-300 px-4 py-6 text-center text-sm text-slate-400">
                  No hay KPIs fijados — fíjalos abajo, en "Todos los KPIs".
                </div>
              )}
            </div>
          </div>

          {tablaActiva === "activaciones" && (
            <TablaActivaciones items={datos.activaciones.items} />
          )}

          <div>
            <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Todos los KPIs
            </div>
            <div className="space-y-3">
              {[...porGrupo.entries()].map(([grupo, ks]) => (
                <div key={grupo} className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
                  <div className="mb-2 px-1 text-xs font-semibold text-slate-500">{grupo}</div>
                  <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                    {ks.map((k) => {
                      const r = k.calcular(datos, cuenta);
                      const fijado = fijados.includes(k.id);
                      const bloqueado = !fijado && fijados.length >= MAX_FIJADOS;
                      return (
                        <div key={k.id} className="relative">
                          <Kpi label={k.titulo} value={r.value} pie={r.pie} tone={r.tone} />
                          <button
                            type="button"
                            disabled={bloqueado}
                            title={bloqueado ? "Quita uno de arriba para fijar este" : fijado ? "Quitar de arriba" : "Fijar arriba"}
                            onClick={() => alternar(k.id)}
                            className={`absolute right-2 top-2 rounded-md p-1 ${
                              fijado ? "text-indigo-600 hover:bg-indigo-50"
                                     : bloqueado ? "text-slate-300"
                                     : "text-slate-400 hover:bg-slate-100 hover:text-slate-600"}`}
                          >
                            {fijado ? <Pin size={13} /> : <PinOff size={13} />}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function TablaActivaciones({ items }: { items: ItemActivacion[] }) {
  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-100 px-4 py-2.5 text-xs font-semibold text-slate-600">
        Publicaciones activadas en el rango ({items.length})
      </div>
      <div className="max-h-80 overflow-y-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50 text-[10px] uppercase tracking-wider text-slate-500">
              <th className="px-3 py-2 text-left font-semibold">SKU</th>
              <th className="px-3 py-2 text-left font-semibold">Producto</th>
              <th className="px-3 py-2 text-left font-semibold">Cuenta</th>
              <th className="px-3 py-2 text-left font-semibold">Fecha</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it, i) => (
              <tr key={`${it.sku}-${it.cuenta}-${i}`} className="border-b border-slate-50 last:border-0 hover:bg-slate-50">
                <td className="px-3 py-2 font-mono text-[11px] text-slate-700">{it.sku}</td>
                <td className="truncate px-3 py-2 text-[12px] text-slate-600" title={it.titulo ?? ""}>
                  {it.titulo ?? <span className="text-slate-300">sin título</span>}
                </td>
                <td className="px-3 py-2 text-[11px] text-slate-500">{it.cuenta}</td>
                <td className="px-3 py-2 text-[11px] text-slate-500">{fFechaHora(it.date_published)}</td>
              </tr>
            ))}
            {items.length === 0 && (
              <tr><td colSpan={4} className="px-3 py-8 text-center text-sm text-slate-400">
                Ninguna activación en este rango.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
