"use client";

/**
 * /dashboard — Monitoreo de operaciones. Primera pestaña del panel.
 *
 * Hoy contiene el monitor del FAN-OUT de stock DROP: cuando una venta no-FULL
 * descuenta en WooCommerce, el fan-out replica ese número a las publicaciones
 * ACTIVAS y no-FULL de los demás canales (si no, siguen ofreciendo el stock
 * viejo → sobreventa).
 *
 * En DRY-RUN calcula y registra todo SIN escribir en los marketplaces: es el
 * modo para dejarlo corriendo y cazar errores antes de encenderlo de verdad.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRightLeft,
  CheckCircle2,
  Clock,
  Eye,
  Package,
  PauseCircle,
  Power,
  RefreshCw,
  Warehouse,
} from "lucide-react";
import { API_BASE } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";

interface Accion {
  canal: string | null;
  cuenta: string | null;
  item_id: string | null;
  accion: string;
  stock_actual_canal: number | null;
  objetivo?: number | null;
  omitido_por?: string | null;
  resultado?: string | null;
}
interface FilaHistorial {
  ts: string;
  sku: string;
  motivo: string | null;
  dry_run: number;
  stock_drop: number | null;
  objetivo: number | null;
  canal: string | null;
  cuenta: string | null;
  item_id: string | null;
  accion: string;
  stock_canal: number | null;
  resultado: string | null;
  ms: number | null;
}
interface Estado {
  habilitado: boolean;
  dry_run: boolean;
  canales_habilitados: string[] | string;
  escritores_implementados: string[];
  reserva: number;
  debounce_s: number;
  pendientes: string[];
  contadores: Record<string, number>;
  eventos: {
    ts: string; sku: string; motivo: string; dry_run: boolean;
    stock_drop: number | null; objetivo: number | null; acciones: Accion[]; ms: number;
  }[];
  resumen: {
    por_accion?: Record<string, number>;
    eventos?: number; skus?: number; errores?: number;
    desde?: string; hasta?: string;
  };
  historial: FilaHistorial[];
}

const COLOR_ACCION: Record<string, string> = {
  escribir: "bg-emerald-50 text-emerald-700 border-emerald-200",
  sin_cambio: "bg-slate-50 text-slate-500 border-slate-200",
  omitir: "bg-amber-50 text-amber-700 border-amber-200",
  sin_destinos: "bg-slate-50 text-slate-400 border-slate-200",
};

export default function DashboardPage() {
  const [d, setD] = useState<Estado | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [soloErrores, setSoloErrores] = useState(false);

  const cargar = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/fanout/estado`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setD(await r.json());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "error");
    }
  }, []);

  useEffect(() => {
    void cargar();
    const t = setInterval(() => void cargar(), 10_000);   // 10 s
    return () => clearInterval(t);
  }, [cargar]);

  const res = d?.resumen ?? {};
  const acc = res.por_accion ?? {};
  const historial = (d?.historial ?? []).filter(
    (h) => !soloErrores || (h.resultado || "").startsWith("ERROR"),
  );

  return (
    <div className="min-h-screen bg-slate-50">
      <AppNavbar />
      <main className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6">
        {/* Encabezado */}
        <div className="mb-6 rounded-2xl bg-gradient-to-r from-indigo-600 to-violet-600 p-6 text-white shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className="text-[11px] font-bold uppercase tracking-[0.2em] text-indigo-200">
                Monitoreo de operaciones
              </div>
              <h1 className="mt-1 text-3xl font-bold tracking-tight">Fan-out de stock</h1>
              <p className="mt-1 max-w-2xl text-sm text-indigo-100">
                Cuando una venta no-FULL descuenta en WooCommerce, el stock DROP se
                replica a las publicaciones activas de los demás canales.
              </p>
            </div>
            <div className="flex items-center gap-2">
              {d && (
                <>
                  <Estadillo
                    icono={d.habilitado ? <Power size={14} /> : <PauseCircle size={14} />}
                    texto={d.habilitado ? "Encendido" : "Apagado"}
                    tono={d.habilitado ? "ok" : "off"}
                  />
                  <Estadillo
                    icono={<Eye size={14} />}
                    texto={d.dry_run ? "DRY-RUN (no escribe)" : "ESCRIBIENDO"}
                    tono={d.dry_run ? "warn" : "ok"}
                  />
                </>
              )}
              <button
                onClick={() => void cargar()}
                className="rounded-lg bg-white/15 p-2 transition-colors hover:bg-white/25"
                title="Refrescar"
              >
                <RefreshCw size={16} />
              </button>
            </div>
          </div>
        </div>

        {error && (
          <div className="mb-4 flex items-center gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            <AlertTriangle size={16} /> No se pudo leer el estado: {error}
          </div>
        )}

        {/* Métricas */}
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <Metrica titulo="Eventos registrados" valor={res.eventos ?? 0} icono={<Activity size={16} />} />
          <Metrica titulo="SKUs tocados" valor={res.skus ?? 0} icono={<Package size={16} />} />
          <Metrica titulo="Escrituras planeadas" valor={acc["escribir"] ?? 0} icono={<ArrowRightLeft size={16} />} tono="ok" />
          <Metrica titulo="Omitidos (FULL/pausados)" valor={acc["omitir"] ?? 0} icono={<Warehouse size={16} />} tono="warn" />
          <Metrica titulo="Errores" valor={res.errores ?? 0} icono={<AlertTriangle size={16} />} tono={(res.errores ?? 0) > 0 ? "err" : "ok"} />
        </div>

        {/* Configuración + cola */}
        {d && (
          <div className="mb-6 grid gap-3 lg:grid-cols-3">
            <Tarjeta titulo="Configuración">
              <Dato k="Canales habilitados" v={Array.isArray(d.canales_habilitados) ? d.canales_habilitados.join(", ") : d.canales_habilitados} />
              <Dato k="Escritores listos" v={d.escritores_implementados.join(", ") || "—"} />
              <Dato k="Colchón (reserva)" v={`${d.reserva} pzas`} />
              <Dato k="Debounce" v={`${d.debounce_s} s`} />
            </Tarjeta>
            <Tarjeta titulo="Cola ahora">
              {d.pendientes.length ? (
                <div className="flex flex-wrap gap-1">
                  {d.pendientes.map((s) => (
                    <span key={s} className="rounded-md bg-indigo-50 px-2 py-0.5 font-mono text-[11px] text-indigo-700">{s}</span>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-slate-400">Sin SKUs pendientes.</p>
              )}
              <div className="mt-2 flex items-center gap-1.5 text-[11px] text-slate-400">
                <Clock size={12} /> Las ráfagas del mismo SKU se funden en una escritura.
              </div>
            </Tarjeta>
            <Tarjeta titulo="Ventana observada">
              <Dato k="Desde" v={res.desde ? String(res.desde).slice(0, 19) : "—"} />
              <Dato k="Hasta" v={res.hasta ? String(res.hasta).slice(0, 19) : "—"} />
              <Dato k="Sin cambio" v={String(acc["sin_cambio"] ?? 0)} />
            </Tarjeta>
          </div>
        )}

        {/* Bitácora */}
        <section className="rounded-2xl border border-slate-200 bg-white">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
            <h2 className="text-sm font-bold text-slate-800">
              Bitácora {d?.dry_run && <span className="text-amber-600">· simulación</span>}
            </h2>
            <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-500">
              <input type="checkbox" checked={soloErrores} onChange={(e) => setSoloErrores(e.target.checked)} className="h-3.5 w-3.5" />
              Solo errores
            </label>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-slate-50 text-[11px] uppercase tracking-wider text-slate-400">
                <tr>
                  <th className="px-4 py-2 font-semibold">Hora</th>
                  <th className="px-4 py-2 font-semibold">SKU</th>
                  <th className="px-4 py-2 font-semibold">Canal</th>
                  <th className="px-4 py-2 font-semibold">Acción</th>
                  <th className="px-4 py-2 font-semibold">Stock</th>
                  <th className="px-4 py-2 font-semibold">Resultado</th>
                </tr>
              </thead>
              <tbody>
                {historial.length === 0 && (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-sm text-slate-400">
                    Sin movimientos todavía. Aparecerán aquí en cuanto haya una venta no-FULL.
                  </td></tr>
                )}
                {historial.map((h, i) => (
                  <tr key={i} className="border-t border-slate-50 hover:bg-slate-50/60">
                    <td className="whitespace-nowrap px-4 py-2 font-mono text-[11px] text-slate-500">{String(h.ts).slice(5, 19)}</td>
                    <td className="whitespace-nowrap px-4 py-2 font-mono text-xs font-semibold text-slate-700">{h.sku}</td>
                    <td className="whitespace-nowrap px-4 py-2 text-xs text-slate-600">
                      {h.canal ?? "—"}{h.cuenta ? ` · ${h.cuenta}` : ""}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2">
                      <span className={`rounded-md border px-1.5 py-0.5 text-[10px] font-semibold ${COLOR_ACCION[h.accion] ?? "border-slate-200 bg-slate-50 text-slate-500"}`}>
                        {h.accion}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-4 py-2 text-xs text-slate-600">
                      {h.stock_canal ?? "—"} → <strong className="text-slate-800">{h.objetivo ?? "—"}</strong>
                    </td>
                    <td className="px-4 py-2 text-xs text-slate-500">
                      {(h.resultado || "").startsWith("ERROR") ? (
                        <span className="text-rose-600">{h.resultado}</span>
                      ) : (h.resultado || "").includes("DRY-RUN") ? (
                        <span className="flex items-center gap-1 text-amber-600"><Eye size={11} /> {h.resultado}</span>
                      ) : (
                        <span className="flex items-center gap-1"><CheckCircle2 size={11} className="text-emerald-500" /> {h.resultado}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  );
}

/* ── piezas ─────────────────────────────────────────────────────────────── */

function Estadillo({ icono, texto, tono }: { icono: React.ReactNode; texto: string; tono: "ok" | "warn" | "off" }) {
  const c = tono === "ok" ? "bg-emerald-400/20 text-emerald-50"
    : tono === "warn" ? "bg-amber-400/25 text-amber-50" : "bg-white/15 text-white/80";
  return (
    <span className={`flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold ${c}`}>
      {icono} {texto}
    </span>
  );
}

function Metrica({ titulo, valor, icono, tono = "neutro" }: {
  titulo: string; valor: number | string; icono: React.ReactNode; tono?: "ok" | "warn" | "err" | "neutro";
}) {
  const c = tono === "ok" ? "text-emerald-600" : tono === "warn" ? "text-amber-600"
    : tono === "err" ? "text-rose-600" : "text-slate-800";
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      <div className="mb-1 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
        {icono} {titulo}
      </div>
      <div className={`text-2xl font-bold ${c}`}>{valor}</div>
    </div>
  );
}

function Tarjeta({ titulo, children }: { titulo: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="mb-2 text-[11px] font-bold uppercase tracking-[0.15em] text-slate-400">{titulo}</div>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function Dato({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-sm">
      <span className="text-slate-500">{k}</span>
      <span className="font-semibold text-slate-800">{v}</span>
    </div>
  );
}
