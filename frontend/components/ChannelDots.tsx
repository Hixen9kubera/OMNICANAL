"use client";

import type { CanalResumen } from "@/lib/types";

interface Props {
  canales: CanalResumen[];
  colorMap: Record<string, string>;
  labelMap: Record<string, string>;
}

/**
 * Puntos de colores que indican en qué marketplaces está publicado un SKU
 * (como en la columna CANALES de la UI actual). Lleno = publicado, hueco = no.
 */
export default function ChannelDots({ canales, colorMap, labelMap }: Props) {
  if (!canales.length) {
    return <span className="text-xs text-slate-300">—</span>;
  }
  return (
    <div className="flex items-center gap-1.5">
      {canales.map((c) => {
        const color = colorMap[c.canal] ?? "#94a3b8";
        return (
          <span
            key={c.canal + (c.item_id ?? "")}
            title={`${labelMap[c.canal] ?? c.canal}${c.publicado ? " · publicado" : " · sin publicar"}`}
            className="inline-block h-3 w-3 rounded-full border-2"
            style={{
              backgroundColor: c.publicado ? color : "transparent",
              borderColor: color,
            }}
          />
        );
      })}
    </div>
  );
}
