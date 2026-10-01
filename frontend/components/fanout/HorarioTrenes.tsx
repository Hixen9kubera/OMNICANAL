"use client";

/**
 * HorarioTrenes — una fila por cambio de stock, la más nueva arriba, con lo que
 * contestó cada canal. Las filas que llegan en el sondeo entran llenándose canal
 * por canal. Tocar una fila abre su rastro.
 */
import { Check, ChevronRight, X } from "lucide-react";
import type { Celda, Columna, Evento } from "./tipos";
import { CELDA_CLS } from "./tipos";

const ORIGEN: Record<Evento["origen"], string> = {
  odoo: "bg-indigo-50 text-indigo-800",
  woo: "bg-cyan-50 text-cyan-800",
  venta: "bg-amber-50 text-amber-800",
  otro: "bg-slate-100 text-slate-700",
};

function CeldaChip({ c, demora, nueva }: { c: Celda; demora: number; nueva: boolean }) {
  return (
    <span title={c.detalle || undefined}
      className={`flex min-h-8 items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-medium leading-4 ${CELDA_CLS[c.k]} ${nueva ? "fo-revelar" : ""}`}
      style={nueva ? { animationDelay: `${demora}s` } : undefined}>
      {c.k === "ok" && <Check size={13} strokeWidth={2.5} className="shrink-0" aria-hidden />}
      {c.k === "mal" && <X size={13} strokeWidth={2.5} className="shrink-0" aria-hidden />}
      <span className="truncate">{c.k === "nopub" ? "no publicada" : c.texto}</span>
    </span>
  );
}

export default function HorarioTrenes({ eventos, columnas, nuevos, ocultos, onAbrir }: {
  eventos: Evento[];
  columnas: Columna[];
  nuevos: Set<number>;
  ocultos: number;
  onAbrir: (e: Evento) => void;
}) {
  const plantilla = `76px minmax(190px,1.3fr) repeat(${columnas.length}, minmax(124px,1fr)) 56px`;
  return (
    <div className="overflow-x-auto">
      <style>{`
        @keyframes fo-entrar { from { opacity: 0; transform: translateY(-6px) } to { opacity: 1; transform: none } }
        @keyframes fo-celda { from { opacity: 0 } to { opacity: 1 } }
        @keyframes fo-resaltar { 0%, 35% { background-color: #eef2ff } 100% { background-color: transparent } }
        .fo-fila-nueva { animation: fo-entrar .45s ease-out both, fo-resaltar 4.5s ease-out; }
        .fo-revelar { animation: fo-celda .35s ease-out both; }
        @media (prefers-reduced-motion: reduce) {
          .fo-fila-nueva { animation: none; background-color: #eef2ff; }
          .fo-revelar { animation: none; }
        }
      `}</style>
      <div className="flex min-w-[900px] flex-col">
        <div className="grid gap-2 border-b border-slate-200 px-6 py-1.5 text-[11px] font-semibold uppercase leading-4 tracking-wide text-slate-500"
          style={{ gridTemplateColumns: plantilla }}>
          <span>Hora</span><span>Cambio</span>
          {columnas.map((c) => <span key={c.id}>{c.nombre}</span>)}
          <span className="text-right">Tardó</span>
        </div>
        {eventos.length === 0 && (
          <p className="px-6 py-6 text-sm text-slate-500">Todavía no hay cambios en la ventana.</p>
        )}
        {eventos.map((e) => {
          const nueva = nuevos.has(e.id);
          return (
            <button key={`${e.sku}-${e.fin}`} type="button" onClick={() => onAbrir(e)}
              className={`grid w-full items-center gap-2 border-b border-slate-100 px-6 py-2 text-left transition-colors hover:bg-slate-50 focus-visible:bg-indigo-50 focus-visible:outline-none ${nueva ? "fo-fila-nueva" : ""}`}
              style={{ gridTemplateColumns: plantilla }}>
              <span className="font-mono text-xs text-slate-600">{e.hora}</span>
              <span className="flex min-w-0 flex-col">
                <span className="truncate font-mono text-xs font-semibold leading-4 text-slate-900">{e.sku}</span>
                <span className="mt-0.5 flex items-center gap-1.5">
                  <span className={`rounded px-1.5 text-[11px] font-medium leading-4 ${ORIGEN[e.origen]}`}>{e.cambio}</span>
                </span>
              </span>
              {columnas.map((c, i) => (
                <CeldaChip key={c.id} c={e.celdas[c.id] ?? { k: "nopub", texto: "—" }} demora={0.35 + i * 0.3} nueva={nueva} />
              ))}
              <span className="flex items-center justify-end gap-1 text-xs text-slate-600">
                {e.total_s != null ? `${Math.round(e.total_s)} s` : "—"}
                <ChevronRight size={14} className="text-slate-400" aria-hidden />
              </span>
            </button>
          );
        })}
        {ocultos > 0 && (
          <p className="px-6 pt-2 text-xs text-slate-500">
            En la última hora, {ocultos} {ocultos === 1 ? "cambio no tenía" : "cambios no tenían"} publicaciones en el reparto: no había a quién escribirle.
          </p>
        )}
      </div>
    </div>
  );
}
