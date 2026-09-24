"use client";

/**
 * Buscar SKUs para ponerlos en la planeación (Brandon, 24-sep: "primeramente poder
 * buscar los SKUs para ponerlos en la planeación semanal"). Busca SÓLO entre lo
 * PUBLICADO en la tienda elegida —cada cuenta tiene sus SKUs—, por SKU, por nombre
 * o pegando varios separados por coma. Mercado Libre se verifica EN VIVO: lo que
 * no está en la copia se le pregunta a ML por SKU.
 */

import { useState } from "react";
import { CheckCircle2, Plus, Search } from "lucide-react";
import { API_BASE, fetchSesion } from "@/lib/api";
import { FONDO_RAYADO, num } from "./ui";
import type { FilaPlan, Tienda, TiendaPlan } from "./tipos";

export default function BuscarSku({ tiendas, ventana, enPlan, onAgregar }: {
  tiendas: { t: Tienda; d: TiendaPlan }[];
  ventana: number;
  /** Claves «tienda|sku» que ya están en la planeación. */
  enPlan: Set<string>;
  onAgregar: (filas: FilaPlan[]) => void;
}) {
  const [tienda, setTienda] = useState<Tienda | null>(tiendas[0]?.t ?? null);
  const [texto, setTexto] = useState("");
  const [res, setRes] = useState<{ filas: FilaPlan[]; no_publicados: string[] } | null>(null);
  const [cargando, setCargando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const actual = tienda && tiendas.some((x) => x.t === tienda) ? tienda : tiendas[0]?.t ?? null;

  const buscar = async () => {
    if (!actual || texto.trim().length < 2) return;
    setCargando(true);
    setError(null);
    try {
      const url = `${API_BASE}/api/fulfillment/crear-full/buscar?tienda=${encodeURIComponent(actual)}`
        + `&q=${encodeURIComponent(texto.trim())}&ventana=${ventana}`;
      const r = await fetchSesion(url, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}: ${(await r.text()).slice(0, 200)}`);
      setRes(await r.json());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCargando(false);
    }
  };

  const nuevos = (res?.filas ?? []).filter((f) => !enPlan.has(`${f.tienda}|${f.sku}`));
  return (
    <div className="rounded-xl border border-indigo-100 bg-indigo-50/40 px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-bold uppercase tracking-[.06em] text-indigo-700">Agregar SKUs</span>
        <select value={actual ?? ""} onChange={(ev) => { setTienda(ev.target.value as Tienda); setRes(null); }}
                className="rounded-lg border border-slate-200 bg-white px-2 py-1.5 text-xs font-semibold text-slate-700">
          {tiendas.map(({ t, d }) => <option key={t} value={t}>{d.nombre}</option>)}
        </select>
        <label className="flex min-w-[260px] flex-1 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5">
          <Search className="h-3.5 w-3.5 text-slate-400" />
          <input value={texto} onChange={(ev) => setTexto(ev.target.value)}
                 onKeyDown={(ev) => { if (ev.key === "Enter") void buscar(); }}
                 placeholder="SKU, nombre, o varios SKUs separados por coma"
                 className="w-full bg-transparent text-xs text-slate-700 placeholder:text-slate-400 focus:outline-none" />
        </label>
        <button type="button" onClick={() => void buscar()} disabled={cargando || texto.trim().length < 2 || !actual}
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-indigo-700 disabled:opacity-40">
          {cargando ? "Buscando en vivo…" : "Buscar"}
        </button>
        {nuevos.length > 1 && (
          <button type="button" onClick={() => { onAgregar(nuevos); setRes(null); setTexto(""); }}
                  className="rounded-lg border border-indigo-200 bg-white px-3 py-1.5 text-xs font-bold text-indigo-700 hover:bg-indigo-50">
            Agregar los {nuevos.length}
          </button>
        )}
      </div>
      {error && <p className="mt-2 text-[12px] text-rose-700">No se pudo buscar: {error}</p>}
      {res && (
        <div className="mt-2 max-h-[240px] overflow-y-auto rounded-lg border border-slate-200 bg-white">
          {res.filas.map((f) => {
            const ya = enPlan.has(`${f.tienda}|${f.sku}`);
            const libre = f.libre ? Object.values(f.libre).reduce((a, b) => a + b, 0) : null;
            return (
              <div key={f.sku} className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-3 py-1.5 text-[12px] first:border-t-0">
                <span className="min-w-0">
                  <span className="font-mono font-bold text-slate-800">{f.sku}</span>
                  <span className="text-slate-400"> · {(f.nombre ?? f.titulo_mkt ?? "").slice(0, 70)}</span>
                  <span className="block text-[11px] text-slate-500">
                    {f.verificada ? <span className="text-emerald-700">publicada, verificada en vivo</span> : "publicada (copia del sync)"}
                    {" "}· vendió {num(f.vv)} · en almacén {f.stock === null ? "?" : num(f.stock)}
                    {" "}· libre en Odoo {libre === null ? "no está en Odoo" : num(libre)}
                  </span>
                </span>
                {ya ? (
                  <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700">
                    <CheckCircle2 className="h-3.5 w-3.5" /> ya en la planeación
                  </span>
                ) : (
                  <button type="button" onClick={() => onAgregar([f])}
                          className="inline-flex items-center gap-1 rounded-md border border-indigo-200 px-2 py-1 text-[11px] font-bold text-indigo-700 hover:bg-indigo-50">
                    <Plus className="h-3 w-3" /> Agregar
                  </button>
                )}
              </div>
            );
          })}
          {res.filas.length === 0 && (
            <p className="px-3 py-2 text-[12px] text-slate-500" style={{ background: FONDO_RAYADO }}>
              Nada publicado en esta tienda con «{texto}».
            </p>
          )}
          {res.no_publicados.length > 0 && (
            <p className="border-t border-slate-100 px-3 py-2 text-[11.5px] text-amber-800">
              No están publicados en esta tienda (Mercado Libre lo confirmó en vivo cuando aplica):{" "}
              <span className="font-mono">{res.no_publicados.join(", ")}</span>
            </p>
          )}
        </div>
      )}
    </div>
  );
}
