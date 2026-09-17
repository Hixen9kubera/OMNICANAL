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
   del mismo panel se separan a la primera corrección que solo se haga en una.

   `claro` y `bloque` (14-sep-2026) son para la nota de la regla de precios, que
   vive en el cajón del producto: una tarjeta blanca con secciones, sobre un
   bloque y no sobre una celda. El fijo también ahí es obligatorio: la sección
   del canal lleva overflow-hidden y un panel absoluto se cortaría en su borde.

   `envoltura` (17-sep-2026) es para las pestañas del stepper de /omnicanal:
   cada una es un botón dentro de una fila flex, y el `w-full` de siempre haría
   que cada envoltura pidiera el ancho entero de la fila. */

import { useCallback, useState } from "react";

export default function PanelHover({ children, panel, ancho = 290, alto = 190, claro = false, bloque = false, envoltura }: {
  children: React.ReactNode; panel: React.ReactNode; ancho?: number;
  /** Alto aproximado del panel: decide si se pinta abajo o arriba. */
  alto?: number;
  /** Tarjeta blanca con borde en vez del globo oscuro. */
  claro?: boolean;
  /** Envuelve un bloque (`div`) en vez de una celda (`span` en línea). */
  bloque?: boolean;
  /** Clases del `div` envolvente cuando `bloque`, en lugar de las de siempre. */
  envoltura?: string;
}) {
  const [pos, setPos] = useState<{ x: number; y: number; arriba: boolean; w: number } | null>(null);
  const abrir = useCallback((el: HTMLElement) => {
    const r = el.getBoundingClientRect();
    // En una pantalla angosta el panel se encoge antes que salirse de ella.
    const w = Math.min(ancho, window.innerWidth - 16);
    const medio = w / 2 + 8;
    const x = Math.min(Math.max(r.left + r.width / 2, medio), window.innerWidth - medio);
    const arriba = r.bottom + alto > window.innerHeight;
    setPos({ x, y: arriba ? r.top - 8 : r.bottom + 8, arriba, w });
  }, [ancho, alto]);

  const Globo = bloque ? "div" : "span";
  const globo = pos && (
    <Globo
      role="tooltip"
      style={{
        left: pos.x, top: pos.y, width: pos.w,
        transform: `translateX(-50%)${pos.arriba ? " translateY(-100%)" : ""}`,
      }}
      className={[
        "pointer-events-none fixed z-50 block whitespace-normal break-words text-left font-normal normal-case tracking-normal",
        claro
          ? "rounded-[10px] border border-slate-200 bg-white px-3.5 py-3 text-slate-700 shadow-card-hover"
          : "rounded-lg bg-slate-900 px-3 py-2 text-[11px] leading-snug text-slate-100 shadow-xl",
      ].join(" ")}
    >
      {panel}
    </Globo>
  );
  const eventos = {
    onMouseEnter: (e: React.MouseEvent<HTMLElement>) => abrir(e.currentTarget),
    onMouseLeave: () => setPos(null),
  };

  return bloque ? (
    <div className={envoltura ?? "block w-full cursor-help"} {...eventos}>
      {children}
      {globo}
    </div>
  ) : (
    <span className="inline-block w-full cursor-help" {...eventos}>
      {children}
      {globo}
    </span>
  );
}
