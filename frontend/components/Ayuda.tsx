"use client";

/**
 * Ayuda — insignia "?" con descripción al pasar el cursor.
 *
 * Se posiciona FIJO calculando el rect de la insignia, no `absolute`: las
 * tablas viven dentro de contenedores con overflow-x-auto y un tooltip
 * absoluto quedaría recortado por el borde de la tabla (mismo motivo que el
 * submenú del navbar).
 *
 * El clic se detiene aquí (stopPropagation): las cabeceras de tabla ordenan al
 * hacer clic, y pedir ayuda no debe reordenar nada.
 */

import { useCallback, useState } from "react";

export default function Ayuda({ texto, titulo }: { texto: string; titulo?: string }) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);

  const abrir = useCallback((el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    // Se centra bajo la insignia pero sin salirse de la pantalla: el tooltip
    // mide 260 px, así que el centro se acota a 134 px de cada borde.
    const medio = 134;
    const x = Math.min(Math.max(r.left + r.width / 2, medio), window.innerWidth - medio);
    setPos({ x, y: r.bottom + 8 });
  }, []);

  return (
    <>
      <span
        role="button"
        tabIndex={0}
        aria-label={`Qué significa: ${titulo ?? ""}`}
        onMouseEnter={(e) => abrir(e.currentTarget)}
        onMouseLeave={() => setPos(null)}
        onFocus={(e) => abrir(e.currentTarget)}
        onBlur={() => setPos(null)}
        onClick={(e) => { e.stopPropagation(); e.preventDefault(); }}
        className="ml-1 inline-flex h-[13px] w-[13px] cursor-help select-none items-center justify-center rounded-full border border-slate-300 align-middle text-[9px] font-bold leading-none text-slate-400 transition-colors hover:border-indigo-400 hover:bg-indigo-50 hover:text-indigo-600"
      >
        ?
      </span>
      {pos && (
        <span
          role="tooltip"
          style={{ left: pos.x, top: pos.y, transform: "translateX(-50%)" }}
          /* whitespace-normal es OBLIGATORIO: las cabeceras de tabla llevan
             whitespace-nowrap y el tooltip lo hereda — el texto salía en una
             sola línea y se cortaba contra el borde del recuadro. Lo mismo con
             normal-case / tracking-normal, que deshacen el estilo de cabecera. */
          className="pointer-events-none fixed z-50 w-[260px] whitespace-normal break-words rounded-lg bg-slate-900 px-3 py-2 text-left text-[11px] font-normal normal-case leading-snug tracking-normal text-slate-100 shadow-xl"
        >
          {titulo && (
            <span className="mb-0.5 block text-[11px] font-semibold text-white">{titulo}</span>
          )}
          {texto}
        </span>
      )}
    </>
  );
}
