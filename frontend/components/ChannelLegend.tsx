"use client";

import { useEffect, useRef, useState } from "react";
import { Info, X } from "lucide-react";
import type { CanalInfo } from "@/lib/types";

interface Props {
  canales: CanalInfo[];
}

/**
 * Tarjeta de información desplegable que explica los "puntos de canales":
 *  ● relleno   = publicado en ese canal
 *  ○ borde     = existe pero sin publicar
 *  (sin punto) = no está en ese canal
 */
export default function ChannelLegend({ canales }: Props) {
  const [abierto, setAbierto] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setAbierto(false);
    };
    if (abierto) document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [abierto]);

  // Solo los canales reales (no los "próximamente") para la leyenda de colores
  const reales = canales.filter((c) => c.habilitado);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setAbierto((v) => !v)}
        className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-600 transition-colors hover:bg-slate-50"
      >
        <Info size={15} />
        Canales
      </button>

      {abierto && (
        <div className="absolute right-0 z-30 mt-2 w-80 animate-fade-in rounded-2xl border border-slate-200 bg-white p-4 shadow-card-hover">
          <div className="mb-3 flex items-center justify-between">
            <h4 className="text-sm font-bold text-slate-800">¿Qué significan los puntos?</h4>
            <button onClick={() => setAbierto(false)} className="text-slate-400 hover:text-slate-600">
              <X size={16} />
            </button>
          </div>

          {/* Estados del punto */}
          <div className="space-y-2.5 rounded-xl bg-slate-50 p-3">
            <div className="flex items-center gap-3 text-sm">
              <span className="inline-block h-3.5 w-3.5 shrink-0 rounded-full border-2 border-indigo-500 bg-indigo-500" />
              <span className="text-slate-700">
                <strong>Relleno</strong> — publicado en ese canal
              </span>
            </div>
            <div className="flex items-center gap-3 text-sm">
              <span className="inline-block h-3.5 w-3.5 shrink-0 rounded-full border-2 border-indigo-500 bg-transparent" />
              <span className="text-slate-700">
                <strong>Solo borde</strong> — existe pero sin publicar (pausado)
              </span>
            </div>
            <div className="flex items-center gap-3 text-sm">
              <span className="inline-flex h-3.5 w-3.5 shrink-0 items-center justify-center text-slate-300">
                —
              </span>
              <span className="text-slate-700">
                <strong>Sin punto</strong> — no está en ese canal
              </span>
            </div>
          </div>

          {/* Color de cada canal */}
          <div className="mt-3">
            <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
              Color por canal
            </div>
            <div className="grid grid-cols-2 gap-2">
              {reales.map((c) => (
                <div key={c.id} className="flex items-center gap-2 text-sm text-slate-600">
                  <span
                    className="inline-block h-3 w-3 rounded-full border-2"
                    style={{ backgroundColor: c.color, borderColor: c.color }}
                  />
                  {c.label}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
