"use client";

/**
 * Administración del catálogo de TEMPORADAS (/analisis/temporadas).
 *
 * La temporada es solo un rango de fechas con nombre: las cifras que se ven en
 * el modal de precio/margen se derivan de los pedidos del rango al momento de
 * consultar. Por eso aquí se puede corregir una fecha (caso Hot Sale/Buen Fin
 * cuando sale el anuncio oficial) o agregar una retroactiva, y todo lo que
 * dependa de ella se recalcula solo — no hay dato que migrar.
 */

import { useCallback, useEffect, useState } from "react";
import { CalendarRange, Check, Loader2, Plus } from "lucide-react";
import { API_BASE } from "@/lib/api";

interface Temporada {
  id: number | null;
  nombre: string;
  anio: number;
  fecha_inicio: string;
  fecha_fin: string;
  fuente: string;
  activa: boolean;
  notas: string | null;
}

const NUEVA = (): Temporada => ({
  id: null, nombre: "", anio: new Date().getFullYear(),
  fecha_inicio: "", fecha_fin: "", fuente: "manual", activa: true, notas: null,
});

export default function TemporadasPage() {
  const [filas, setFilas] = useState<Temporada[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [guardando, setGuardando] = useState<number | "nueva" | null>(null);
  const [ok, setOk] = useState<number | "nueva" | null>(null);
  const [nueva, setNueva] = useState<Temporada>(NUEVA());

  const cargar = useCallback(() => {
    fetch(`${API_BASE}/api/fulfillment/temporadas`, { cache: "no-store" })
      .then((r) => { if (!r.ok) throw new Error(`API ${r.status}`); return r.json(); })
      .then((d) => setFilas(d.temporadas))
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(cargar, [cargar]);

  const guardar = async (t: Temporada, clave: number | "nueva") => {
    setGuardando(clave); setOk(null); setErr(null);
    try {
      const r = await fetch(`${API_BASE}/api/fulfillment/temporadas`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(t),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => null);
        throw new Error(j?.detail ?? `API ${r.status}`);
      }
      setOk(clave);
      if (clave === "nueva") setNueva(NUEVA());
      cargar();
      setTimeout(() => setOk(null), 2500);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setGuardando(null);
    }
  };

  const editar = (id: number, campo: keyof Temporada, valor: unknown) =>
    setFilas((fs) => (fs ?? []).map((f) => (f.id === id ? { ...f, [campo]: valor } : f)));

  const inputCls =
    "rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 focus:border-indigo-400 focus:outline-none";

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-1 flex items-center gap-2">
        <CalendarRange size={16} className="text-indigo-500" />
        <h2 className="text-sm font-bold text-slate-800">Temporadas comerciales</h2>
      </div>
      <p className="mb-4 max-w-3xl text-xs text-slate-500">
        Cada temporada es un rango de fechas con nombre. Las ventas, el precio y el
        margen por temporada se calculan de los pedidos reales de ese rango, así que
        corregir una fecha aquí actualiza todo automáticamente — también hacia el
        pasado. Las temporadas <b>sembradas</b> se generan solas cada año; Hot Sale y
        Buen Fin traen la fecha típica y se corrigen cuando sale el anuncio oficial.
      </p>

      {err && (
        <div className="mb-3 rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700">
          {err}
        </div>
      )}
      {!filas && !err && (
        <div className="flex h-40 items-center justify-center text-sm text-slate-400">Cargando…</div>
      )}

      {filas && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[820px] text-xs">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wide text-slate-400">
                <th className="px-2 py-2 font-semibold">Nombre</th>
                <th className="px-2 py-2 font-semibold">Año</th>
                <th className="px-2 py-2 font-semibold">Inicio</th>
                <th className="px-2 py-2 font-semibold">Fin</th>
                <th className="px-2 py-2 font-semibold">Origen</th>
                <th className="px-2 py-2 text-center font-semibold">Activa</th>
                <th className="px-2 py-2 font-semibold">Notas</th>
                <th className="px-2 py-2" />
              </tr>
            </thead>
            <tbody>
              {/* Alta de una temporada nueva */}
              <tr className="border-b border-slate-100 bg-indigo-50/40">
                <td className="px-2 py-2">
                  <input className={inputCls} placeholder="Liquidación marzo…" value={nueva.nombre}
                         onChange={(e) => setNueva({ ...nueva, nombre: e.target.value })} />
                </td>
                <td className="px-2 py-2">
                  <input className={`${inputCls} w-16`} type="number" value={nueva.anio}
                         onChange={(e) => setNueva({ ...nueva, anio: Number(e.target.value) })} />
                </td>
                <td className="px-2 py-2">
                  <input className={inputCls} type="date" value={nueva.fecha_inicio}
                         onChange={(e) => setNueva({ ...nueva, fecha_inicio: e.target.value })} />
                </td>
                <td className="px-2 py-2">
                  <input className={inputCls} type="date" value={nueva.fecha_fin}
                         onChange={(e) => setNueva({ ...nueva, fecha_fin: e.target.value })} />
                </td>
                <td className="px-2 py-2 text-slate-400">manual</td>
                <td className="px-2 py-2 text-center">—</td>
                <td className="px-2 py-2">
                  <input className={`${inputCls} w-full`} placeholder="opcional" value={nueva.notas ?? ""}
                         onChange={(e) => setNueva({ ...nueva, notas: e.target.value || null })} />
                </td>
                <td className="px-2 py-2 text-right">
                  <button
                    disabled={!nueva.nombre || !nueva.fecha_inicio || !nueva.fecha_fin || guardando === "nueva"}
                    onClick={() => guardar(nueva, "nueva")}
                    className="inline-flex items-center gap-1 rounded-lg bg-indigo-600 px-2.5 py-1.5 text-[11px] font-bold text-white transition-colors hover:bg-indigo-700 disabled:opacity-40">
                    {guardando === "nueva" ? <Loader2 size={12} className="animate-spin" />
                      : ok === "nueva" ? <Check size={12} /> : <Plus size={12} />}
                    Agregar
                  </button>
                </td>
              </tr>

              {filas.map((t) => (
                <tr key={t.id} className={`border-b border-slate-50 ${t.activa ? "" : "opacity-50"}`}>
                  <td className="px-2 py-1.5">
                    <input className={inputCls} value={t.nombre}
                           onChange={(e) => editar(t.id!, "nombre", e.target.value)} />
                  </td>
                  <td className="px-2 py-1.5 tabular-nums text-slate-500">{t.anio}</td>
                  <td className="px-2 py-1.5">
                    <input className={inputCls} type="date" value={t.fecha_inicio}
                           onChange={(e) => editar(t.id!, "fecha_inicio", e.target.value)} />
                  </td>
                  <td className="px-2 py-1.5">
                    <input className={inputCls} type="date" value={t.fecha_fin}
                           onChange={(e) => editar(t.id!, "fecha_fin", e.target.value)} />
                  </td>
                  <td className="px-2 py-1.5">
                    <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold ${t.fuente === "sembrada" ? "bg-slate-100 text-slate-500" : "bg-violet-100 text-violet-700"}`}>
                      {t.fuente}
                    </span>
                  </td>
                  <td className="px-2 py-1.5 text-center">
                    <input type="checkbox" checked={t.activa}
                           onChange={(e) => editar(t.id!, "activa", e.target.checked)} />
                  </td>
                  <td className="max-w-[220px] truncate px-2 py-1.5 text-slate-400" title={t.notas ?? ""}>
                    {t.notas ?? "—"}
                  </td>
                  <td className="px-2 py-1.5 text-right">
                    <button onClick={() => guardar(t, t.id!)} disabled={guardando === t.id}
                            className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-1.5 text-[11px] font-bold text-slate-600 transition-colors hover:bg-slate-50">
                      {guardando === t.id ? <Loader2 size={12} className="animate-spin" />
                        : ok === t.id ? <Check size={12} className="text-emerald-600" /> : null}
                      Guardar
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
