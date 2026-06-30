"use client";

import { ImageIcon, PackageCheck, PackageX, Truck } from "lucide-react";
import type { Producto } from "@/lib/types";
import ChannelDots from "./ChannelDots";

interface Props {
  productos: Producto[];
  esGeneral: boolean;
  cargando: boolean;
  color: string;
  colorMap: Record<string, string>;
  labelMap: Record<string, string>;
  onSelect: (p: Producto) => void;
}

function precioMXN(v: number | null): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", {
    style: "currency",
    currency: "MXN",
    maximumFractionDigits: 0,
  }).format(v);
}

export default function ProductList({
  productos,
  esGeneral,
  cargando,
  color,
  colorMap,
  labelMap,
  onSelect,
}: Props) {
  if (cargando) {
    return (
      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        {Array.from({ length: 12 }).map((_, i) => (
          <div key={i} className="flex animate-pulse items-center gap-4 border-b border-slate-100 px-4 py-3">
            <div className="h-12 w-12 rounded-lg bg-slate-100" />
            <div className="h-4 w-24 rounded bg-slate-100" />
            <div className="h-4 flex-1 rounded bg-slate-100" />
            <div className="h-4 w-20 rounded bg-slate-100" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-card">
      <table className="w-full min-w-[820px] text-sm">
        <thead>
          <tr className="border-b border-slate-200 bg-slate-50 text-left text-[11px] uppercase tracking-wide text-slate-500">
            <th className="px-4 py-3 font-semibold">Producto</th>
            <th className="px-3 py-3 font-semibold">Categoría</th>
            <th className="px-3 py-3 text-right font-semibold">Precio</th>
            <th className="px-3 py-3 text-center font-semibold">Stock</th>
            <th className="px-3 py-3 text-center font-semibold">Estado</th>
            <th className="px-4 py-3 text-center font-semibold">
              {esGeneral ? "Canales" : "Logística"}
            </th>
          </tr>
        </thead>
        <tbody>
          {productos.map((p) => {
            const sinStock = p.stock_real !== null && (p.stock_real ?? 0) <= 0;
            return (
              <tr
                key={`${p.sku}-${p.item_id ?? ""}`}
                onClick={() => onSelect(p)}
                className="cursor-pointer border-b border-slate-100 transition-colors hover:bg-slate-50"
              >
                {/* Producto */}
                <td className="px-4 py-2.5">
                  <div className="flex items-center gap-3">
                    <div className="flex h-12 w-12 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-slate-100 bg-slate-50">
                      {p.imagen ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={p.imagen} alt="" loading="lazy" className="h-full w-full object-contain" />
                      ) : (
                        <ImageIcon size={20} className="text-slate-300" />
                      )}
                    </div>
                    <div className="min-w-0">
                      <div className="truncate font-semibold text-slate-800">{p.nombre}</div>
                      <span className="font-mono text-[11px] text-slate-400">{p.sku}</span>
                    </div>
                  </div>
                </td>
                {/* Categoría */}
                <td className="px-3 py-2.5 text-xs text-slate-500">
                  <span className="line-clamp-1">
                    {p.categoria_path.map((c) => c.nombre).join(" › ") || "—"}
                  </span>
                </td>
                {/* Precio */}
                <td className="px-3 py-2.5 text-right font-bold text-slate-900">
                  {precioMXN(p.precio)}
                </td>
                {/* Stock */}
                <td className="px-3 py-2.5 text-center">
                  <div className="flex flex-col items-center gap-0.5">
                    <span className={sinStock ? "font-bold text-red-600" : "font-bold text-emerald-600"}>
                      {p.stock_real ?? p.stock ?? "—"}
                    </span>
                    <div className="flex gap-1">
                      {!!p.stock_full && p.stock_full > 0 && (
                        <span className="rounded bg-amber-100 px-1 text-[9px] font-bold text-amber-700">
                          FULL {p.stock_full}
                        </span>
                      )}
                      {!!p.stock_fba && p.stock_fba > 0 && (
                        <span className="rounded bg-sky-100 px-1 text-[9px] font-bold text-sky-700">
                          FBA {p.stock_fba}
                        </span>
                      )}
                    </div>
                  </div>
                </td>
                {/* Estado */}
                <td className="px-3 py-2.5 text-center">
                  {p.publicado ? (
                    <span
                      className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[10px] font-bold text-white"
                      style={{ backgroundColor: color }}
                    >
                      <PackageCheck size={11} /> Publicado
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 rounded-full bg-slate-200 px-2 py-1 text-[10px] font-bold text-slate-600">
                      <PackageX size={11} /> {p.situacion ?? "Sin publicar"}
                    </span>
                  )}
                </td>
                {/* Canales / Logística */}
                <td className="px-4 py-2.5">
                  <div className="flex justify-center">
                    {esGeneral ? (
                      <ChannelDots canales={p.canales} colorMap={colorMap} labelMap={labelMap} />
                    ) : p.full ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-700">
                        <Truck size={11} /> {p.full_label}
                      </span>
                    ) : (
                      <span className="text-xs text-slate-400">—</span>
                    )}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
