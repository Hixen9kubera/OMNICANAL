"use client";

/**
 * El selector de semanas del Checklist de almacén, por NÚMERO («Week 39»), con
 * una palomita en cada semana que ya tiene SKUs cargados. Lo usan el Checklist
 * y el Catálogo Maestro.
 *
 * La palomita sale de `ops.checklist_lote` (¿la semana tiene SKUs?); no hay
 * otra tabla. Las semanas son ISO (lunes a domingo): 2026 tiene 53.
 */
import { useEffect, useRef, useState } from "react";
import { CalendarDays, Check, ChevronDown, ChevronLeft, ChevronRight, Loader2 } from "lucide-react";

import { mensajeDeError, semanasChecklist } from "@/lib/api";
import type { SemanasChecklist } from "@/lib/types";

const MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];

/** '2026-09-21' → '21 sep'. Anclado al mediodía UTC: la zona no lo mueve. */
function dia(iso: string): string {
  const d = new Date(`${iso.slice(0, 10)}T12:00:00Z`);
  return `${d.getUTCDate()} ${MESES[d.getUTCMonth()]}`;
}

function masDias(iso: string, n: number): string {
  const d = new Date(`${iso.slice(0, 10)}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** El año ISO de una semana es el de su jueves. */
function anioIso(lunes: string): number {
  return new Date(`${masDias(lunes, 3)}T12:00:00Z`).getUTCFullYear();
}

export default function SelectorSemana({
  valor, onElegir, soloCargadas, vacio = "Semana",
}: {
  /** El lunes elegido (YYYY-MM-DD), o null si no hay. */
  valor: string | null;
  onElegir: (lunes: string) => void;
  /** En el Maestro solo tiene sentido elegir una semana con SKUs. */
  soloCargadas?: boolean;
  vacio?: string;
}) {
  const [abierto, setAbierto] = useState(false);
  const [anio, setAnio] = useState<number | null>(valor ? anioIso(valor) : null);
  const [datos, setDatos] = useState<SemanasChecklist | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const caja = useRef<HTMLDivElement>(null);

  // Al cambiar la semana desde fuera (flechas, «Esta semana») y al cerrar sin
  // elegir, el año vuelve al de la semana elegida: si no, el botón se quedaba
  // sin número después de hojear otro año.
  useEffect(() => { if (valor && !abierto) setAnio(anioIso(valor)); }, [valor, abierto]);

  useEffect(() => {
    const ctrl = new AbortController();
    setCargando(true);
    setError(null);
    semanasChecklist(anio ?? undefined, ctrl.signal)
      .then((d) => {
        if (!d.ok) { setError(d.motivo ?? "No se pudieron leer las semanas."); return; }
        setDatos(d);
        if (anio === null) setAnio(d.anio);
      })
      .catch((e: unknown) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        setError(mensajeDeError(e, "No se pudieron leer las semanas."));
      })
      .finally(() => setCargando(false));
    return () => ctrl.abort();
  // Se vuelve a leer al abrir: otra persona pudo cargar una semana.
  }, [anio, abierto]);

  useEffect(() => {
    if (!abierto) return;
    const fuera = (e: MouseEvent) => {
      if (caja.current && !caja.current.contains(e.target as Node)) setAbierto(false);
    };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setAbierto(false); };
    document.addEventListener("mousedown", fuera);
    window.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", fuera);
      window.removeEventListener("keydown", esc);
    };
  }, [abierto]);

  const deEsteAnio = datos && datos.anio === anio ? datos.semanas : [];
  const actual = datos?.semanas.find((s) => s.semana === valor);
  const cargadas = deEsteAnio.filter((s) => s.cargada);
  // Topes: del primer año con lotes al siguiente del último (o del de hoy).
  const anios = datos?.anios ?? [];

  return (
    <div className="relative" ref={caja}>
      <button type="button" onClick={() => setAbierto((a) => !a)} aria-expanded={abierto}
              className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50">
        <CalendarDays className="h-4 w-4 text-slate-400" />
        {valor ? `Week ${actual?.numero ?? ""}`.trim() : vacio}
        {actual?.cargada && (
          <span className="inline-flex items-center gap-0.5 rounded-full bg-emerald-50 px-1.5 text-[11px] font-bold text-emerald-700">
            <Check className="h-3 w-3" /> {actual.skus}
          </span>
        )}
        <ChevronDown className={`h-3.5 w-3.5 text-slate-400 transition ${abierto ? "rotate-180" : ""}`} />
      </button>

      {abierto && (
        <div className="absolute left-0 z-40 mt-1.5 w-[22rem] rounded-2xl border border-slate-200 bg-white p-3 shadow-xl max-sm:fixed max-sm:inset-x-4 max-sm:top-24 max-sm:w-auto">
          <div className="flex items-center justify-between">
            <button type="button" disabled={!anio || (anios.length > 0 && anio <= Math.min(...anios))}
                    onClick={() => setAnio((a) => (a ? a - 1 : a))}
                    className="rounded-lg p-1 text-slate-500 hover:bg-slate-100 disabled:opacity-30" title="Año anterior">
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="text-sm font-extrabold text-slate-800">
              {anio ?? "—"}
              {cargando && <Loader2 className="ml-1.5 inline h-3.5 w-3.5 animate-spin text-slate-400" />}
            </span>
            <button type="button" disabled={!anio || (anios.length > 0 && anio >= Math.max(...anios) + 1)}
                    onClick={() => setAnio((a) => (a ? a + 1 : a))}
                    className="rounded-lg p-1 text-slate-500 hover:bg-slate-100 disabled:opacity-30" title="Año siguiente">
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>

          {error && <p className="mt-2 rounded-lg bg-rose-50 p-2 text-xs text-rose-700">{error}</p>}

          <div className="mt-2 grid grid-cols-8 gap-1">
            {deEsteAnio.map((s) => {
              const elegida = s.semana === valor;
              const bloqueada = soloCargadas && !s.cargada;
              return (
                <button
                  key={s.semana} type="button" disabled={bloqueada}
                  onClick={() => { onElegir(s.semana); setAbierto(false); }}
                  title={`Week ${s.numero} · ${dia(s.semana)} al ${dia(masDias(s.semana, 6))}${
                    s.cargada ? ` · ${s.skus} SKUs cargados` : " · sin SKUs"}${s.actual ? " · esta semana" : ""}`}
                  className={`relative flex h-9 flex-col items-center justify-center rounded-lg text-xs font-bold transition ${
                    elegida ? "bg-indigo-600 text-white"
                      : s.cargada ? "bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200 hover:bg-emerald-100"
                        : "text-slate-500 hover:bg-slate-100"} ${
                    s.actual && !elegida ? "ring-2 ring-indigo-300" : ""} disabled:cursor-not-allowed disabled:opacity-40`}
                >
                  {s.numero}
                  {s.cargada && (
                    <Check className={`absolute right-0.5 top-0.5 h-2.5 w-2.5 ${elegida ? "text-white" : "text-emerald-600"}`} />
                  )}
                </button>
              );
            })}
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
            <span className="inline-flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded bg-emerald-100 ring-1 ring-emerald-300" /> con SKUs cargados
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="inline-block h-2.5 w-2.5 rounded ring-2 ring-indigo-300" /> esta semana
            </span>
          </div>

          {cargadas.length > 0 && (
            <div className="mt-2 border-t border-slate-100 pt-2">
              <div className="text-[10px] font-bold uppercase tracking-[0.06em] text-slate-400">
                Cargadas en {anio}
              </div>
              <div className="mt-1 flex flex-wrap gap-1">
                {cargadas.map((s) => (
                  <button key={s.semana} type="button"
                          onClick={() => { onElegir(s.semana); setAbierto(false); }}
                          className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ${
                            s.semana === valor ? "bg-indigo-600 text-white ring-indigo-600"
                              : "bg-white text-slate-600 ring-slate-200 hover:bg-slate-50"}`}>
                    <Check className="h-3 w-3" /> Week {s.numero} · {s.skus}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
