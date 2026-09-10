"use client";

import { ChevronDown, ChevronRight, Layers, Split } from "lucide-react";
import type { ModoPublicacion, Producto, VarianteResumen } from "@/lib/types";
import ChannelDots from "./ChannelDots";
import ChipRevision from "./ChipRevision";
import { TituloMoneda } from "./Moneda";

/**
 * Piezas de "Padre / Único + variantes", calcadas de la vista Crear Productos
 * (frontend/app/crear/page.tsx). Aquí se comparten para que Productos y
 * Omnicanal muestren exactamente lo mismo, con los mismos colores y reglas.
 */

/** Misma regla que Crear Productos: es padre si es variable Y tiene variantes. */
export function esPadre(p: Producto): boolean {
  return (p.tipo === "padre" || p.tipo === "variable") && (p.variantes?.length ?? 0) > 0;
}

function precioMXN(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" }).format(v);
}

// ── Columna TIPO ─────────────────────────────────────────────────────────────
export function TipoBadge({ padre, onClick }: { padre: boolean; onClick?: () => void }) {
  const contenido = padre ? (
    <span className="inline-flex items-center gap-1 rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-xs font-bold text-violet-700">
      <Layers size={13} /> Padre
    </span>
  ) : (
    <span className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-semibold text-slate-500">
      Único
    </span>
  );
  if (!onClick) return contenido;
  return (
    <button type="button" onClick={onClick} className="cursor-pointer">
      {contenido}
    </button>
  );
}

// ── Columna VARIANTES ────────────────────────────────────────────────────────
export function VariantesBoton({
  n,
  abierto,
  onClick,
}: {
  n: number;
  abierto: boolean;
  onClick: (e: React.MouseEvent) => void;
}) {
  if (n <= 0) return <span className="text-xs text-slate-300">—</span>;
  return (
    <button
      type="button"
      onClick={onClick}
      title="Ver todas las variantes del padre"
      className="inline-flex items-center gap-1 rounded-lg border border-violet-200 bg-white px-2.5 py-1.5 text-xs font-bold text-violet-700 transition-colors hover:bg-violet-50"
    >
      <span className="tabular-nums">{n}</span>
      <span className="font-semibold">{abierto ? "Ocultar" : "Ver variantes"}</span>
      {abierto ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
    </button>
  );
}

// ── Chip de MODO de publicación ─────────────────────────────────────────────
//
// Sólo se pinta cuando la familia tiene un modo GUARDADO para ese canal. Si no
// hay nada guardado está en el de omisión (individual) y el chip no aparece:
// escribir "individual" en los 1,504 padres sería ruido que no informa de nada.
export function ChipModo({ modo, canalLabel }: {
  modo: ModoPublicacion;
  canalLabel: string;
}) {
  const agrupada = modo === "agrupada";
  return (
    <span
      title={agrupada
        ? `Sale como UNA publicación con selector de variante en ${canalLabel}`
        : `Sale como una publicación POR VARIANTE en ${canalLabel}`}
      className={[
        "mt-1 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold",
        agrupada ? "bg-emerald-50 text-emerald-700" : "bg-orange-50 text-orange-800",
      ].join(" ")}
    >
      {agrupada ? <Layers size={10} /> : <Split size={10} />}
      {agrupada ? "agrupada" : "individual"} en {canalLabel}
    </span>
  );
}

// ── Tabla de variantes (el recuadro que se despliega) ────────────────────────
export function VariantesTabla({
  variantes,
  colorMap,
  labelMap,
  onVariante,
}: {
  variantes: VarianteResumen[];
  colorMap: Record<string, string>;
  labelMap: Record<string, string>;
  /**
   * Clic en un renglón → abre el Estudio con el rail puesto en ESA variante.
   * Opcional a propósito: esta tabla la comparten Productos y Omnicanal, y sin
   * el manejador se comporta exactamente como siempre.
   */
  onVariante?: (sku: string) => void;
}) {
  return (
    <div className="rounded-xl border border-violet-100 bg-white p-3">
      <div className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-violet-600">
        <Layers size={13} /> {variantes.length} variante(s)
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-xs">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wide text-slate-400">
              <th className="py-1 pr-3 font-semibold">SKU</th>
              <th className="py-1 pr-3 font-semibold">Variante</th>
              {/* El costo de la variante es el TOTAL en pesos, no la compra en
                  dólares que se captura en el Estudio. */}
              <th className="py-1 pr-3 text-right font-semibold">
                <TituloMoneda moneda="MXN">Costo</TituloMoneda>
              </th>
              <th className="py-1 pr-3 text-center font-semibold">Stock</th>
              <th className="py-1 text-center font-semibold">Canales</th>
            </tr>
          </thead>
          <tbody>
            {variantes.map((v) => (
              <tr
                key={v.sku}
                onClick={onVariante ? () => onVariante(v.sku) : undefined}
                title={onVariante ? "Abrir el Estudio en esta variante" : undefined}
                className={[
                  "border-t border-slate-100",
                  onVariante ? "cursor-pointer transition-colors hover:bg-violet-50/60" : "",
                ].join(" ")}
              >
                <td className="py-1.5 pr-3 font-mono text-slate-500">
                  <span className="inline-flex items-center gap-1.5">
                    {v.sku}
                    {/* La marca es de ESTA variante: aquí se ve cuál es la validada. */}
                    <ChipRevision revisadoAt={v.revisado_at}
                                  revisadoPor={v.revisado_por}
                                  movida={v.revision_movida} />
                  </span>
                </td>
                <td className="py-1.5 pr-3 font-medium text-slate-700">{v.nombre ?? "—"}</td>
                <td
                  className={
                    v.costo_propio === false
                      ? "py-1.5 pr-3 text-right font-normal italic text-slate-400"
                      : "py-1.5 pr-3 text-right font-semibold text-slate-800"
                  }
                  title={
                    v.costo_propio === false
                      ? "Esta variante no tiene costo capturado — se muestra el del producto padre"
                      : undefined
                  }
                >
                  {precioMXN(v.costo)}
                  {v.costo_propio === false && v.costo != null && (
                    <span className="ml-1 text-[10px] uppercase tracking-wide">heredado</span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-center text-slate-600">{v.stock ?? "—"}</td>
                <td className="py-1.5">
                  <div className="flex justify-center">
                    <ChannelDots
                      canales={v.canales ?? []}
                      colorMap={colorMap}
                      labelMap={labelMap}
                    />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
