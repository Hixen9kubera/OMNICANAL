"use client";

/* ── PANEL AL PASAR EL CURSOR ──────────────────────────────────────────────
   Un `title` nativo solo sabe pintar texto corrido, y comisión y envío no son
   un número: son un número POR CANAL. Este panel muestra el desglose sin pedir
   un clic (Eduardo, 10-ago) — abrir una ventana modal para leer dos renglones
   sería peor que el problema.

   Va POSICIONADO FIJO calculando el rect de la celda, no `absolute`: la tabla
   vive dentro de un contenedor con overflow-x-auto y un panel absoluto quedaría
   recortado por su borde (misma razón que en components/Ayuda.tsx). Y se pinta
   ARRIBA de la celda cuando no cabe abajo, que es lo normal en las últimas
   filas de la página.

   VIVE AQUÍ Y NO EN analisis/page.tsx desde el 14-ago: el popup de "Productos
   más vendidos" tiene que enseñar las cuentas igual que la tabla, y dos copias
   del mismo panel se separan a la primera corrección que solo se haga en una. */

import { useCallback, useState } from "react";

export default function PanelHover({ children, panel, ancho = 290 }: {
  children: React.ReactNode; panel: React.ReactNode; ancho?: number;
}) {
  const [pos, setPos] = useState<{ x: number; y: number; arriba: boolean } | null>(null);
  const abrir = useCallback((el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    const medio = ancho / 2 + 8;
    const x = Math.min(Math.max(r.left + r.width / 2, medio), window.innerWidth - medio);
    const arriba = r.bottom + 190 > window.innerHeight;
    setPos({ x, y: arriba ? r.top - 8 : r.bottom + 8, arriba });
  }, [ancho]);
  return (
    <span className="inline-block w-full cursor-help"
          onMouseEnter={(e) => abrir(e.currentTarget)}
          onMouseLeave={() => setPos(null)}>
      {children}
      {pos && (
        <span
          role="tooltip"
          style={{
            left: pos.x, top: pos.y, width: ancho,
            transform: `translateX(-50%)${pos.arriba ? " translateY(-100%)" : ""}`,
          }}
          className="pointer-events-none fixed z-50 block whitespace-normal break-words rounded-lg bg-slate-900 px-3 py-2 text-left text-[11px] font-normal normal-case leading-snug tracking-normal text-slate-100 shadow-xl"
        >
          {panel}
        </span>
      )}
    </span>
  );
}
