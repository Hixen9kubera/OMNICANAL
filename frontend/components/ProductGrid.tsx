"use client";

import { PackageSearch } from "lucide-react";
import type { CanalInfo, Producto } from "@/lib/types";
import ProductCard from "./ProductCard";

interface Props {
  productos: Producto[];
  canal: string;
  esGeneral: boolean;
  cargando: boolean;
  color: string;
  colorMap: Record<string, string>;
  labelMap: Record<string, string>;
  onSelect: (p: Producto) => void;
}

// Esqueleto de carga (40 tarjetas)
function Skeleton({ n }: { n: number }) {
  return (
    <>
      {Array.from({ length: n }).map((_, i) => (
        <div
          key={i}
          className="flex animate-pulse flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white"
        >
          <div className="aspect-square w-full bg-slate-100" />
          <div className="space-y-2 p-3.5">
            <div className="h-3 w-1/3 rounded bg-slate-100" />
            <div className="h-4 w-full rounded bg-slate-100" />
            <div className="h-4 w-2/3 rounded bg-slate-100" />
            <div className="h-6 w-1/2 rounded bg-slate-100" />
          </div>
        </div>
      ))}
    </>
  );
}

export default function ProductGrid({
  productos,
  canal,
  esGeneral,
  cargando,
  color,
  colorMap,
  labelMap,
  onSelect,
}: Props) {
  if (!cargando && productos.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-slate-200 bg-white py-24 text-center">
        <PackageSearch size={48} className="text-slate-300" strokeWidth={1.3} />
        <p className="text-base font-semibold text-slate-600">
          No se encontraron productos
        </p>
        <p className="text-sm text-slate-400">
          Prueba con otra búsqueda o cambia de canal.
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
      {cargando ? (
        <Skeleton n={40} />
      ) : (
        productos.map((p) => (
          <ProductCard
            key={`${canal}-${p.sku}-${p.item_id ?? ""}`}
            producto={p}
            canal={canal}
            esGeneral={esGeneral}
            color={color}
            colorMap={colorMap}
            labelMap={labelMap}
            onClick={() => onSelect(p)}
          />
        ))
      )}
    </div>
  );
}
