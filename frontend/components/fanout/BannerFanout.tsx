/**
 * El banner de la casa para las páginas del fan-out: gradiente 120° del índigo
 * (#4F46E5 → #818CF8), rounded-3xl, el círculo blanco al 20% arriba a la derecha
 * y el contador grande — el mismo que Costos, Competencia y las demás pestañas.
 * Las acciones van del lado del texto, como el «Cómo leer» de las otras páginas.
 */
import type { ReactNode } from "react";

/** Botón o etiqueta translúcida sobre el banner. */
export const ACCION_BANNER =
  "flex items-center gap-2 rounded-xl bg-white/15 px-3 py-2 text-xs font-semibold text-white ring-1 ring-white/30 backdrop-blur";

export default function BannerFanout({ icono, titulo, texto, acciones, cifra, cifraTexto }: {
  icono: ReactNode;
  titulo: string;
  texto: string;
  acciones?: ReactNode;
  cifra?: ReactNode;
  cifraTexto?: string;
}) {
  return (
    <div className="relative overflow-hidden rounded-3xl p-6 shadow-card"
      style={{ background: "linear-gradient(120deg, #4F46E5 0%, #818CF8 100%)", color: "#FFFFFF" }}>
      <div className="relative z-10 flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <div className="text-xs font-semibold uppercase tracking-[0.2em] opacity-80">Centro Omnicanal · Operaciones</div>
          <h1 className="mt-1 flex items-center gap-2 text-3xl font-extrabold tracking-tight">{icono} {titulo}</h1>
          <p className="mt-1 max-w-2xl text-sm opacity-90">{texto}</p>
          {acciones && <div className="mt-3 flex flex-wrap items-center gap-2">{acciones}</div>}
        </div>
        {cifra != null && (
          <div className="text-right">
            <div className="text-4xl font-black tabular-nums">{cifra}</div>
            {cifraTexto && <div className="text-xs font-semibold uppercase tracking-wide opacity-80">{cifraTexto}</div>}
          </div>
        )}
      </div>
      <div className="pointer-events-none absolute -right-16 -top-16 h-56 w-56 rounded-full opacity-20"
        style={{ background: "#FFFFFF" }} />
    </div>
  );
}
