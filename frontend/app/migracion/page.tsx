"use client";

/**
 * /migracion — Avance en tiempo real del espejo (dual-write) hacia la BD
 * centralizada "kubera", explícito a nivel archivo.py → tabla.
 *
 * - Tarjeta por escritor .py (censo de services/kubera_mirror.py): estado del
 *   espejo, contadores ok/error, latencia media y último evento.
 * - Feed de eventos en vivo (poll cada 5 s, ring buffer del backend).
 * - "Errores para limpieza": agrupados por (archivo, tabla, tipo) desde MySQL
 *   `espejo_kubera_log` — esa lista ES el plan de trabajo de la limpieza.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CalendarCheck,
  CheckCircle2,
  ChevronDown,
  Database,
  ShieldCheck,
  Power,
} from "lucide-react";
import { API_BASE } from "@/lib/api";
import AppNavbar from "@/components/AppNavbar";

/* ── Tipos que devuelve /api/migracion/* ───────────────────────────────── */

interface Escritor {
  archivo: string;
  funcion: string;
  tabla_mysql: string;
  tabla_kubera: string | null;
  operacion: string;
  disparador: string;
  estado: string;
  estado_espejo: string;
  nota?: string;
  ok: number;
  error: number;
  latencia_ms: number | null;
  ultimo?: Evento | null;
}

interface Evento {
  ts: string;
  archivo: string;
  funcion: string;
  tabla_origen: string;
  tabla_destino: string | null;
  operacion: string;
  clave: string | null;
  ok: boolean;
  ms: number;
  error_tipo: string | null;
  error_texto: string | null;
}

interface Estado {
  flags: {
    KUBERA_MIRROR_ENABLED: boolean;
    KUBERA_DB_URL_definida: boolean;
    KUBERA_MIRROR_TABLAS: string[] | null;
  };
  escritores: Escritor[];
  totales: { ok: number; error: number };
}

interface GrupoError {
  archivo_py: string;
  tabla_origen: string;
  tabla_destino: string | null;
  error_tipo: string;
  n: number;
  abiertos: number;
  ultimo_ts: string;
  ejemplo: string | null;
  ejemplo_clave: string | null;
  ejemplo_payload: string | null;
}

/* Camino al corte: actas de migration.reconciliation_runs (crons 06:30/06:45) */
interface DeltaDominio {
  dominio: string;
  etiqueta: string;
  racha: number;
  objetivo: number;
  ultima: { ts: string; resultado: string } | null;
  historial: { fecha: string; resultado: string }[];
}

interface DeltasResp {
  disponible: boolean;
  objetivo: number;
  dominios: DeltaDominio[];
}

/* ── Estilo de los estados (verde=ok, rojo=error, ámbar=apagado, gris=gap) ── */

const ESTILO_ESTADO: Record<string, { chip: string; label: string }> = {
  activo: { chip: "bg-emerald-100 text-emerald-700", label: "Espejo activo" },
  apagado: { chip: "bg-amber-100 text-amber-700", label: "Apagado (flag)" },
  cubierto_por_companero: { chip: "bg-sky-100 text-sky-700", label: "Cubierto (compañero)" },
  gap_sin_destino: { chip: "bg-slate-200 text-slate-600", label: "GAP sin destino v4" },
  no_aplica: { chip: "bg-slate-100 text-slate-500", label: "No aplica (caché)" },
  bloqueado: { chip: "bg-slate-200 text-slate-600", label: "Bloqueado (P3)" },
};

const fmtHora = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("es-MX", { hour12: false }) : "—";

export default function MigracionPage() {
  const [estado, setEstado] = useState<Estado | null>(null);
  const [eventos, setEventos] = useState<Evento[]>([]);
  const [grupos, setGrupos] = useState<GrupoError[]>([]);
  const [tab, setTab] = useState<"espejo" | "errores">("espejo");
  const [conResueltos, setConResueltos] = useState(false);
  const [errCarga, setErrCarga] = useState<string | null>(null);
  const [resolviendo, setResolviendo] = useState<string | null>(null);
  const [deltas, setDeltas] = useState<DeltasResp | null>(null);

  const cargar = useCallback(async () => {
    try {
      const [e, ev] = await Promise.all([
        fetch(`${API_BASE}/api/migracion/estado`, { cache: "no-store" }).then((r) => r.json()),
        fetch(`${API_BASE}/api/migracion/eventos?limit=120`, { cache: "no-store" }).then((r) => r.json()),
      ]);
      setEstado(e);
      setEventos(ev.eventos || []);
      setErrCarga(null);
    } catch (e) {
      setErrCarga(String(e));
    }
  }, []);

  const cargarErrores = useCallback(async () => {
    try {
      const r = await fetch(
        `${API_BASE}/api/migracion/errores?incluir_resueltos=${conResueltos}`,
        { cache: "no-store" },
      ).then((x) => x.json());
      setGrupos(r.grupos || []);
    } catch {
      /* la vista de errores es best-effort */
    }
  }, [conResueltos]);

  const cargarDeltas = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/api/migracion/deltas`, { cache: "no-store" });
      setDeltas(await r.json());
    } catch {
      /* vista informativa: sin actas no se rompe la página */
    }
  }, []);

  useEffect(() => {
    cargar();
    cargarErrores();
    cargarDeltas();
    const t = setInterval(cargar, 5000);          // feed en vivo cada 5 s
    const t2 = setInterval(cargarErrores, 15000); // errores cada 15 s
    const t3 = setInterval(cargarDeltas, 60000);  // actas cada 60 s (cambian 1 vez al día)
    return () => {
      clearInterval(t);
      clearInterval(t2);
      clearInterval(t3);
    };
  }, [cargar, cargarErrores, cargarDeltas]);

  /* Serie de actividad del espejo: eventos ok/error por minuto (últimos 30) */
  const serie = useMemo(() => {
    const MIN = 30;
    const ahora = Date.now();
    const buckets = Array.from({ length: MIN }, (_, i) => ({
      t: ahora - (MIN - 1 - i) * 60000,
      ok: 0,
      error: 0,
    }));
    for (const ev of eventos) {
      const idx = MIN - 1 - Math.floor((ahora - new Date(ev.ts).getTime()) / 60000);
      if (idx >= 0 && idx < MIN) {
        if (ev.ok) buckets[idx].ok += 1;
        else buckets[idx].error += 1;
      }
    }
    return buckets;
  }, [eventos]);
  const maxSerie = Math.max(1, ...serie.map((b) => b.ok + b.error));

  const resolver = async (g: GrupoError) => {
    const key = `${g.archivo_py}|${g.tabla_origen}|${g.error_tipo}`;
    setResolviendo(key);
    try {
      await fetch(`${API_BASE}/api/migracion/errores/resolver`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          archivo_py: g.archivo_py,
          tabla_origen: g.tabla_origen,
          error_tipo: g.error_tipo,
        }),
      });
      await cargarErrores();
    } finally {
      setResolviendo(null);
    }
  };

  const flags = estado?.flags;
  const abiertos = useMemo(
    () => grupos.reduce((s, g) => s + (g.abiertos ?? g.n), 0),
    [grupos],
  );

  return (
    <div className="min-h-screen">
      <AppNavbar />
      <main className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6">
      {/* Encabezado + flags */}
      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 text-white">
          <Database size={19} />
        </div>
        <div className="mr-auto">
          <h1 className="text-xl font-bold tracking-tight text-slate-900">
            Migración · espejo kubera
          </h1>
          <p className="text-sm text-slate-500">
            Dual-write de los escritores .py hacia la BD centralizada — fase de
            descubrimiento de errores
          </p>
        </div>
        {flags && (
          <div className="flex flex-wrap items-center gap-2 text-xs font-medium">
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 ${
                flags.KUBERA_MIRROR_ENABLED
                  ? "bg-emerald-100 text-emerald-700"
                  : "bg-amber-100 text-amber-700"
              }`}
            >
              <Power size={13} />
              KUBERA_MIRROR_ENABLED: {flags.KUBERA_MIRROR_ENABLED ? "ON" : "OFF"}
            </span>
            <span
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 ${
                flags.KUBERA_DB_URL_definida
                  ? "bg-emerald-100 text-emerald-700"
                  : "bg-rose-100 text-rose-700"
              }`}
            >
              <ShieldCheck size={13} />
              KUBERA_DB_URL {flags.KUBERA_DB_URL_definida ? "definida" : "SIN definir"}
            </span>
            {flags.KUBERA_MIRROR_TABLAS && (
              <span className="rounded-full bg-slate-100 px-3 py-1.5 text-slate-600">
                Solo: {flags.KUBERA_MIRROR_TABLAS.join(", ")}
              </span>
            )}
          </div>
        )}
      </div>

      {errCarga && (
        <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-2.5 text-sm text-rose-700">
          No pude leer /api/migracion: {errCarga}
        </div>
      )}

      {/* Tabs */}
      <div className="mb-5 flex items-center gap-1 border-b border-slate-200">
        {(["espejo", "errores"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`relative -mb-px flex items-center gap-2 px-4 py-2.5 text-sm font-semibold transition-colors ${
              tab === t
                ? "border-b-2 border-indigo-500 text-indigo-600"
                : "text-slate-500 hover:text-slate-800"
            }`}
          >
            {t === "espejo" ? <Activity size={15} /> : <AlertTriangle size={15} />}
            {t === "espejo" ? "Espejo en vivo" : "Errores para limpieza"}
            {t === "errores" && abiertos > 0 && (
              <span className="rounded-full bg-rose-100 px-2 py-0.5 text-[11px] font-bold text-rose-700">
                {abiertos}
              </span>
            )}
          </button>
        ))}
        {estado && (
          <div className="ml-auto pb-2 text-xs text-slate-500">
            Total corrida:{" "}
            <span className="font-semibold text-emerald-600">{estado.totales.ok} ok</span>
            {" · "}
            <span className="font-semibold text-rose-600">{estado.totales.error} error</span>
          </div>
        )}
      </div>

      {tab === "espejo" && (
        <>
          {/* Camino al corte: racha de actas en cero por dominio (criterio: 14) */}
          <h2 className="mb-2 flex items-center gap-2 text-sm font-bold uppercase tracking-wide text-slate-500">
            <CalendarCheck size={15} /> Camino al corte — actas de deltas en cero
          </h2>
          <div className="mb-6 grid gap-3 md:grid-cols-2">
            {!deltas?.disponible && (
              <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-400 md:col-span-2">
                Sin acceso a las actas (BD kubera no configurada en este ambiente).
              </div>
            )}
            {(deltas?.dominios || []).map((d) => {
              const completo = d.racha >= d.objetivo;
              const dias = d.historial.slice(-d.objetivo);
              const faltantes = Math.max(0, d.objetivo - dias.length);
              return (
                <div key={d.dominio} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                  <div className="mb-2 flex items-center gap-3">
                    <span className="text-sm font-bold text-slate-800">{d.etiqueta}</span>
                    <span className="font-mono text-[11px] text-slate-400">{d.dominio}</span>
                    {d.ultima && (
                      <span
                        className={`ml-auto rounded-full px-2.5 py-1 text-[10.5px] font-bold uppercase ${
                          d.ultima.resultado === "ok"
                            ? "bg-emerald-100 text-emerald-700"
                            : "bg-rose-100 text-rose-700"
                        }`}
                      >
                        última: {d.ultima.resultado} · {fmtHora(d.ultima.ts)}
                      </span>
                    )}
                  </div>
                  <div className="mb-2 flex items-baseline gap-2">
                    <span className={`text-2xl font-extrabold tabular-nums ${completo ? "text-emerald-600" : "text-slate-900"}`}>
                      {d.racha}
                    </span>
                    <span className="text-sm text-slate-500">/ {d.objetivo} días consecutivos en cero</span>
                    {completo && (
                      <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-bold text-emerald-700">
                        listo para corte
                      </span>
                    )}
                  </div>
                  <div className="mb-3 h-2 overflow-hidden rounded-full bg-slate-100">
                    <div
                      className={`h-full rounded-full transition-all ${completo ? "bg-emerald-500" : "bg-indigo-500"}`}
                      style={{ width: `${Math.min(100, (d.racha / d.objetivo) * 100)}%` }}
                    />
                  </div>
                  <div className="flex items-center gap-1.5">
                    {Array.from({ length: faltantes }, (_, i) => (
                      <span key={`f${i}`} className="h-3 w-3 rounded-full bg-slate-100" title="sin acta" />
                    ))}
                    {dias.map((h) => (
                      <span
                        key={h.fecha}
                        className={`h-3 w-3 rounded-full ${
                          h.resultado === "ok" ? "bg-emerald-500" : "bg-rose-500"
                        }`}
                        title={`${h.fecha}: ${h.resultado}`}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Actividad del espejo: eventos ok/error por minuto (últimos 30 min) */}
          <h2 className="mb-2 flex items-center gap-2 text-sm font-bold uppercase tracking-wide text-slate-500">
            <BarChart3 size={15} /> Actividad del espejo (30 min)
          </h2>
          <div className="mb-6 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            {serie.every((b) => b.ok + b.error === 0) ? (
              <div className="py-6 text-center text-sm text-slate-400">
                Sin actividad en los últimos 30 minutos — los eventos aparecen aquí
                al instante cuando un escritor espejado guarda algo.
              </div>
            ) : (
              <>
                <div className="flex h-24 items-end gap-[3px]">
                  {serie.map((b) => (
                    <div
                      key={b.t}
                      className="flex flex-1 flex-col justify-end gap-[1px]"
                      title={`${fmtHora(new Date(b.t).toISOString())} — ${b.ok} ok · ${b.error} error`}
                    >
                      {b.error > 0 && (
                        <div
                          className="w-full rounded-t-sm bg-rose-400"
                          style={{ height: `${(b.error / maxSerie) * 100}%` }}
                        />
                      )}
                      {b.ok > 0 && (
                        <div
                          className={`w-full bg-emerald-400 ${b.error === 0 ? "rounded-t-sm" : ""}`}
                          style={{ height: `${(b.ok / maxSerie) * 100}%` }}
                        />
                      )}
                    </div>
                  ))}
                </div>
                <div className="mt-1.5 flex justify-between text-[11px] text-slate-400">
                  <span>{fmtHora(new Date(serie[0].t).toISOString())}</span>
                  <span className="flex items-center gap-3">
                    <span className="flex items-center gap-1">
                      <span className="h-2 w-2 rounded-full bg-emerald-400" /> ok
                    </span>
                    <span className="flex items-center gap-1">
                      <span className="h-2 w-2 rounded-full bg-rose-400" /> error
                    </span>
                  </span>
                  <span>ahora</span>
                </div>
              </>
            )}
          </div>

          {/* Tarjetas por escritor */}
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {(estado?.escritores || []).map((e, i) => {
              const st = ESTILO_ESTADO[e.estado_espejo] || ESTILO_ESTADO.no_aplica;
              return (
                <div
                  key={`${e.archivo}-${e.tabla_mysql}-${i}`}
                  className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
                >
                  <div className="mb-2 flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate font-mono text-[13px] font-semibold text-slate-800">
                        {e.archivo}
                      </div>
                      <div className="truncate font-mono text-[11px] text-slate-400">
                        {e.funcion}
                      </div>
                    </div>
                    <span
                      className={`shrink-0 rounded-full px-2.5 py-1 text-[10.5px] font-bold uppercase tracking-wide ${st.chip}`}
                    >
                      {st.label}
                    </span>
                  </div>
                  <div className="mb-3 font-mono text-[12px] text-slate-600">
                    {e.tabla_mysql}
                    <span className="text-slate-400"> → </span>
                    {e.tabla_kubera || <span className="italic text-slate-400">sin destino</span>}
                    <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                      {e.operacion}
                    </span>
                  </div>
                  <div className="flex items-center gap-4 text-sm">
                    <span className="inline-flex items-center gap-1 font-semibold text-emerald-600">
                      <CheckCircle2 size={14} /> {e.ok}
                    </span>
                    <span className="inline-flex items-center gap-1 font-semibold text-rose-600">
                      <AlertTriangle size={14} /> {e.error}
                    </span>
                    <span className="text-xs text-slate-400">
                      {e.latencia_ms != null ? `${e.latencia_ms} ms prom.` : "sin eventos"}
                    </span>
                    <span className="ml-auto text-xs text-slate-400">
                      {e.ultimo ? fmtHora(e.ultimo.ts) : ""}
                    </span>
                  </div>
                  <div className="mt-2 text-[11px] leading-snug text-slate-400">
                    {e.disparador}
                    {e.nota ? ` · ${e.nota}` : ""}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Feed de eventos */}
          <h2 className="mb-2 mt-8 text-sm font-bold uppercase tracking-wide text-slate-500">
            Feed de eventos (últimos {eventos.length})
          </h2>
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
            <table className="w-full text-left text-[13px]">
              <thead className="border-b border-slate-200 bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-3 py-2">Hora</th>
                  <th className="px-3 py-2">Archivo · función</th>
                  <th className="px-3 py-2">Tabla → destino</th>
                  <th className="px-3 py-2">Op</th>
                  <th className="px-3 py-2">Clave</th>
                  <th className="px-3 py-2">ms</th>
                  <th className="px-3 py-2">Resultado</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {eventos.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-3 py-6 text-center text-slate-400">
                      Sin eventos aún — el espejo está{" "}
                      {flags?.KUBERA_MIRROR_ENABLED ? "esperando escrituras" : "APAGADO"}.
                    </td>
                  </tr>
                )}
                {eventos.map((ev, i) => (
                  <tr key={i} className={ev.ok ? "" : "bg-rose-50/60"}>
                    <td className="whitespace-nowrap px-3 py-1.5 font-mono text-[12px] text-slate-500">
                      {fmtHora(ev.ts)}
                    </td>
                    <td className="px-3 py-1.5">
                      <span className="font-mono text-[12px] text-slate-700">{ev.archivo}</span>
                      <span className="font-mono text-[11px] text-slate-400"> · {ev.funcion}</span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-1.5 font-mono text-[12px] text-slate-600">
                      {ev.tabla_origen} → {ev.tabla_destino}
                    </td>
                    <td className="px-3 py-1.5 text-[11px] text-slate-500">{ev.operacion}</td>
                    <td className="max-w-[160px] truncate px-3 py-1.5 font-mono text-[12px] text-slate-500">
                      {ev.clave || "—"}
                    </td>
                    <td className="px-3 py-1.5 text-[12px] tabular-nums text-slate-500">
                      {ev.ms}
                    </td>
                    <td className="px-3 py-1.5">
                      {ev.ok ? (
                        <span className="inline-flex items-center gap-1 text-[12px] font-semibold text-emerald-600">
                          <CheckCircle2 size={13} /> ok
                        </span>
                      ) : (
                        <details className="group">
                          <summary className="flex cursor-pointer list-none items-center gap-1 text-[12px] font-semibold text-rose-600">
                            <AlertTriangle size={13} /> {ev.error_tipo}
                            <ChevronDown size={12} className="transition-transform group-open:rotate-180" />
                          </summary>
                          <div className="mt-1 max-w-md whitespace-pre-wrap rounded bg-rose-50 p-2 font-mono text-[11px] text-rose-800">
                            {ev.error_texto}
                          </div>
                        </details>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === "errores" && (
        <>
          <label className="mb-3 flex w-fit cursor-pointer items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={conResueltos}
              onChange={(e) => setConResueltos(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-indigo-600"
            />
            Incluir resueltos
          </label>
          <div className="space-y-3">
            {grupos.length === 0 && (
              <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-400">
                Sin errores registrados. Cuando el espejo capture fallos (FKs
                huérfanas, tipos, colisiones) aparecerán aquí agrupados — esta
                lista es el plan de limpieza.
              </div>
            )}
            {grupos.map((g) => {
              const key = `${g.archivo_py}|${g.tabla_origen}|${g.error_tipo}`;
              const resuelto = (g.abiertos ?? g.n) === 0;
              return (
                <div
                  key={key}
                  className={`rounded-xl border bg-white p-4 shadow-sm ${
                    resuelto ? "border-emerald-200 opacity-70" : "border-rose-200"
                  }`}
                >
                  <div className="flex flex-wrap items-center gap-3">
                    <span
                      className={`rounded-full px-2.5 py-1 text-[11px] font-bold ${
                        resuelto ? "bg-emerald-100 text-emerald-700" : "bg-rose-100 text-rose-700"
                      }`}
                    >
                      {resuelto ? "resuelto" : `${g.abiertos ?? g.n} abiertos`}
                    </span>
                    <span className="font-mono text-[13px] font-semibold text-slate-800">
                      {g.archivo_py}
                    </span>
                    <span className="font-mono text-[12px] text-slate-500">
                      {g.tabla_origen} → {g.tabla_destino || "?"}
                    </span>
                    <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-[11px] text-slate-600">
                      {g.error_tipo}
                    </span>
                    <span className="text-[11px] text-slate-400">
                      {g.n} en total · último {fmtHora(g.ultimo_ts)}
                    </span>
                    {!resuelto && (
                      <button
                        onClick={() => resolver(g)}
                        disabled={resolviendo === key}
                        className="ml-auto rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-emerald-700 disabled:opacity-50"
                      >
                        {resolviendo === key ? "Marcando…" : "Marcar resuelto"}
                      </button>
                    )}
                  </div>
                  {g.ejemplo && (
                    <div className="mt-2 whitespace-pre-wrap rounded bg-slate-50 p-2.5 font-mono text-[11.5px] leading-snug text-slate-600">
                      {g.ejemplo_clave ? `[${g.ejemplo_clave}] ` : ""}
                      {g.ejemplo}
                    </div>
                  )}
                  {g.ejemplo_payload && (
                    <details className="mt-1.5">
                      <summary className="cursor-pointer text-[11px] font-semibold text-slate-400 hover:text-slate-600">
                        payload del último ejemplo
                      </summary>
                      <pre className="mt-1 overflow-x-auto rounded bg-slate-900 p-2.5 text-[11px] leading-snug text-slate-100">
                        {g.ejemplo_payload}
                      </pre>
                    </details>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
      </main>
    </div>
  );
}
