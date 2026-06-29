"use client";

import type { SubCuentaInfo } from "@/lib/types";

interface Props {
  subcuentas: SubCuentaInfo[];
  activa: string | null;
  color: string;
  textoColor: string;
  onSelect: (cuenta: string | null) => void;
}

function fmt(n: number | null): string {
  if (n === null || n === undefined) return "";
  return new Intl.NumberFormat("es-MX").format(n);
}

/**
 * Sub-pestañas de cuenta para Mercado Libre (Kubera / San Corpe) + "Todas".
 * Aparecen solo cuando el canal seleccionado tiene varias cuentas.
 */
export default function AccountTabs({
  subcuentas,
  activa,
  color,
  textoColor,
  onSelect,
}: Props) {
  if (!subcuentas.length) return null;

  const opciones: { id: string | null; label: string; total: number | null }[] = [
    ...subcuentas.map((s) => ({ id: s.id, label: s.label, total: s.total_productos })),
    { id: null, label: "Todas", total: null },
  ];

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="mr-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
        Cuenta
      </span>
      <div className="inline-flex rounded-lg bg-slate-100 p-1">
        {opciones.map((o) => {
          const sel = activa === o.id;
          return (
            <button
              key={o.id ?? "todas"}
              type="button"
              onClick={() => onSelect(o.id)}
              style={sel ? { backgroundColor: color, color: textoColor } : undefined}
              className={[
                "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-semibold transition-all",
                sel ? "shadow-sm" : "text-slate-500 hover:text-slate-800",
              ].join(" ")}
            >
              {o.label}
              {o.total !== null && (
                <span
                  className="rounded-full px-1.5 py-0.5 text-[10px] font-bold"
                  style={{
                    backgroundColor: sel ? "rgba(255,255,255,0.25)" : "#FFFFFF",
                    color: sel ? textoColor : "#94a3b8",
                  }}
                >
                  {fmt(o.total)}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
