"use client";

import type { CanalInfo } from "@/lib/types";
import { esClaro } from "@/lib/theme";

interface Props {
  canales: CanalInfo[];
  activo: string;
  onSelect: (canal: string) => void;
  /** Qué decir en una pestaña deshabilitada. En Omnicanal es un canal al que le
   *  faltan credenciales (el texto por omisión); el Publicador las reusa para
   *  «desactivar por lote», donde el porqué es otro. */
  motivoDeshabilitado?: string;
}

function formatoTotal(n: number | null): string {
  if (n === null || n === undefined) return "";
  return new Intl.NumberFormat("es-MX").format(n);
}

/** La pestaña General cuenta con la vista «productos» de Woo —la del
 *  Publicador— y el encabezado y «Todo el catálogo» con la vista «omnicanal».
 *  Hasta el 2-nov-2026 la primera no contaba los borradores y la pastilla lo
 *  decía («· sin borradores», Eduardo, 23-sep); desde que el estado draft se
 *  salta y Crear Productos se apaga, los cuenta, y las dos cifras casi
 *  coinciden. La ayuda dice qué entra para que nadie busque la diferencia. */
const GENERAL = "general";
const AYUDA_GENERAL =
  "Todo lo que lista el Publicador: publicados, pendientes, listos y borradores.";

export default function MarketplaceTabs({ canales, activo, onSelect, motivoDeshabilitado }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {canales.map((c) => {
        const seleccionado = c.id === activo;
        const deshabilitado = !c.habilitado;
        const borde = esClaro(c.color) ? "#E2E4ED" : c.color;

        // Estilos según estado
        let style: React.CSSProperties = {};
        if (seleccionado) {
          style = {
            backgroundColor: c.color,
            color: c.color_texto,
            borderColor: c.color,
            boxShadow: `0 6px 16px -6px ${c.color}`,
          };
        } else if (!deshabilitado) {
          style = { borderColor: borde, color: "#374151" };
        }

        return (
          <button
            key={c.id}
            type="button"
            disabled={deshabilitado}
            onClick={() => !deshabilitado && onSelect(c.id)}
            title={deshabilitado ? (motivoDeshabilitado ?? "Próximamente — pendiente de credenciales") : c.descripcion}
            style={style}
            className={[
              "relative flex items-center gap-2 rounded-full border px-4 py-2 text-sm font-semibold transition-all",
              seleccionado
                ? "scale-[1.02]"
                : deshabilitado
                  ? "cursor-not-allowed border-dashed border-slate-200 bg-slate-50 text-slate-400"
                  : "bg-white hover:-translate-y-0.5 hover:shadow-card",
            ].join(" ")}
          >
            {/* punto de color de marca */}
            <span
              className="h-2.5 w-2.5 rounded-full ring-2 ring-white/40"
              style={{ backgroundColor: seleccionado ? c.color_texto : c.color }}
            />
            <span>{c.label}</span>

            {/* total / próximamente */}
            {deshabilitado ? (
              <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-slate-500">
                Pronto
              </span>
            ) : c.total_productos !== null ? (
              <span
                title={c.id === GENERAL ? AYUDA_GENERAL : undefined}
                className="whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-bold tabular-nums"
                style={{
                  backgroundColor: seleccionado
                    ? "rgba(255,255,255,0.25)"
                    : "#F1F2F7",
                  color: seleccionado ? c.color_texto : "#64748b",
                }}
              >
                {formatoTotal(c.total_productos)}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
