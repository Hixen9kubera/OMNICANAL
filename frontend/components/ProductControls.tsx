"use client";

import { LayoutGrid, List, ArrowDownWideNarrow } from "lucide-react";
import type { CategoriaWC } from "@/lib/api";

export type Vista = "mosaico" | "lista";

interface Props {
  vista: Vista;
  onVista: (v: Vista) => void;
  orden: string;
  onOrden: (o: string) => void;
  esGeneral: boolean;
  categorias: CategoriaWC[];
  categoria: number | null;
  onCategoria: (c: number | null) => void;
  estados: string[];
  onEstados: (e: string[]) => void;
  color: string;
  textoColor: string;
}

const OPCIONES_ORDEN = [
  { v: "reciente", label: "Más recientes" },
  { v: "stock_desc", label: "Stock: mayor a menor" },
  { v: "stock_asc", label: "Stock: menor a mayor" },
  { v: "precio_desc", label: "Precio: mayor a menor" },
  { v: "precio_asc", label: "Precio: menor a mayor" },
];

const ESTADOS = [
  { v: "publicado", label: "Publicados / Activos" },
  { v: "inactivo", label: "Inactivos / Sin publicar" },
];

export default function ProductControls({
  vista, onVista, orden, onOrden, esGeneral,
  categorias, categoria, onCategoria, estados, onEstados, color, textoColor,
}: Props) {
  const toggleEstado = (v: string) => {
    onEstados(estados.includes(v) ? estados.filter((e) => e !== v) : [...estados, v]);
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        {/* Toggle mosaico / lista */}
        <div className="inline-flex overflow-hidden rounded-lg border border-slate-200 bg-white">
          <button
            onClick={() => onVista("mosaico")}
            title="Vista en mosaico"
            style={vista === "mosaico" ? { backgroundColor: color, color: textoColor } : undefined}
            className={[
              "flex items-center gap-1.5 px-3 py-2 text-sm font-semibold transition-colors",
              vista === "mosaico" ? "" : "text-slate-500 hover:bg-slate-50",
            ].join(" ")}
          >
            <LayoutGrid size={16} /> Mosaico
          </button>
          <button
            onClick={() => onVista("lista")}
            title="Vista en lista"
            style={vista === "lista" ? { backgroundColor: color, color: textoColor } : undefined}
            className={[
              "flex items-center gap-1.5 border-l border-slate-200 px-3 py-2 text-sm font-semibold transition-colors",
              vista === "lista" ? "" : "text-slate-500 hover:bg-slate-50",
            ].join(" ")}
          >
            <List size={16} /> Lista
          </button>
        </div>

        {/* Orden */}
        <div className="relative">
          <ArrowDownWideNarrow size={15} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <select
            value={orden}
            onChange={(e) => onOrden(e.target.value)}
            className="appearance-none rounded-lg border border-slate-200 bg-white py-2 pl-8 pr-8 text-sm font-medium text-slate-600 outline-none hover:bg-slate-50"
          >
            {OPCIONES_ORDEN.map((o) => (
              <option key={o.v} value={o.v}>{o.label}</option>
            ))}
          </select>
        </div>

        {/* Categoría (solo General) */}
        {esGeneral && (
          <select
            value={categoria ?? ""}
            onChange={(e) => onCategoria(e.target.value ? Number(e.target.value) : null)}
            className="max-w-[220px] appearance-none rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-600 outline-none hover:bg-slate-50"
          >
            <option value="">Todas las categorías</option>
            {categorias.map((c) => (
              <option key={c.id} value={c.id}>{c.nombre} ({c.count})</option>
            ))}
          </select>
        )}
      </div>

      {/* Filtro inteligente de estado — visible en vista LISTA */}
      {vista === "lista" && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            Filtro inteligente
          </span>
          {ESTADOS.map((e) => {
            const activo = estados.includes(e.v);
            return (
              <button
                key={e.v}
                onClick={() => toggleEstado(e.v)}
                style={activo ? { backgroundColor: color, color: textoColor, borderColor: color } : undefined}
                className={[
                  "rounded-full border px-3 py-1 text-xs font-semibold transition-colors",
                  activo ? "" : "border-slate-200 text-slate-500 hover:bg-slate-50",
                ].join(" ")}
              >
                {e.label}
              </button>
            );
          })}
          {estados.length > 0 && (
            <button
              onClick={() => onEstados([])}
              className="text-xs font-medium text-slate-400 underline hover:text-slate-600"
            >
              Limpiar
            </button>
          )}
          {estados.length === 2 && (
            <span className="text-[11px] text-slate-400">(combinados)</span>
          )}
        </div>
      )}
    </div>
  );
}
