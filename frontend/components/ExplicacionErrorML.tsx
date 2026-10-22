"use client";

import type { ExplicacionErrorML as Item } from "@/lib/types";

/**
 * El rechazo de Mercado Libre en palabras: POR QUÉ y QUÉ HACER, una causa por renglón
 * (`backend/services/ml_errores.py`). Brandon, 22-oct-2026: «veo únicamente los 400».
 * Sin solución conocida se dice el porqué y que no hay arreglo desde el panel; no se inventa.
 */
export default function ExplicacionErrorML({ items, mismoQue }: { items: Item[]; mismoQue?: string }) {
  if (mismoQue) {
    return <span>mismo motivo que en {mismoQue}.</span>;
  }
  return (
    <div className="mt-1 space-y-1.5">
      <div className="font-semibold">Mercado Libre la rechazó.</div>
      {items.map((e, i) => (
        <div key={i} className="rounded-md bg-white/70 px-2 py-1.5 text-xs leading-relaxed text-slate-700">
          <div>
            <span className="font-bold text-red-700">Por qué: </span>
            {e.porque}
          </div>
          <div className="mt-0.5">
            <span className="font-bold text-emerald-700">Solución: </span>
            {e.solucion ?? <span className="text-slate-500">todavía no se puede arreglar desde el panel.</span>}
          </div>
        </div>
      ))}
    </div>
  );
}
