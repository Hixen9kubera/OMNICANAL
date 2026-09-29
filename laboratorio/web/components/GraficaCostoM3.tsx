"use client";

/**
 * $/m³ REAL de cada contenedor (525,000 ÷ m³ totales de su packing list) contra
 * la tarifa de 7,500 del panel. Gráfica de puntos: un punto por contenedor,
 * ordenados de menor a mayor; el eje no arranca en cero a propósito (es una
 * comparación contra una referencia, no una magnitud apilada) y lo dice la
 * línea de referencia.
 *
 * En los contenedores que se llenaron por PESO (≥ 90 % de la carga útil) se
 * dibuja también el W/M de contenedor completo, unido a su punto volumétrico:
 * ese es el costo recomendado ahí, porque los productos densos consumieron la
 * capacidad y los ligeros no deben pagar por ellos.
 *
 * Capa de cursor: el contenedor más cercano al puntero (o con ← →) y un tooltip
 * con sus números. La tabla de contenedores de abajo es su tabla gemela.
 */
import { useMemo, useState } from "react";
import { escalaBonita, Leyenda, useAncho } from "./graficas";
import { SERIE } from "@/lib/tema";
import { cifra, entero, pct, pesos } from "@/lib/formato";
import type { Contenedor } from "@/lib/tipos";

const TARIFA = 7500;
const COLOR_VOL = SERIE.principal;   // #4F46E5
const COLOR_FCL = SERIE.secundaria;  // #0D9488

export const claveCont = (c: Contenedor): string => c.file_id ?? c.codigo;

function nSkus(c: Contenedor): number {
  return Array.isArray(c.skus_en_lote) ? c.skus_en_lote.length : Number(c.skus_en_lote ?? 0);
}

export default function GraficaCostoM3({ contenedores, alto = 150, onElegir, elegido }: {
  contenedores: Contenedor[]; alto?: number; onElegir?: (codigo: string) => void; elegido?: string;
}) {
  const [ref, ancho] = useAncho<HTMLDivElement>();
  const [activo, setActivo] = useState<number | null>(null);
  const xs = useMemo(() => contenedores.filter((c) => c.costo_m3 != null)
    .sort((a, b) => (a.costo_m3 as number) - (b.costo_m3 as number)), [contenedores]);
  if (!xs.length) return null;

  const M = { izq: 50, der: 12, arriba: 14, abajo: 22 };
  const W = Math.max(ancho, 280);
  const valores = xs.flatMap((c) => [c.costo_m3 as number, ...(c.limitado_por_peso && c.costo_m3_wm_fcl != null ? [c.costo_m3_wm_fcl] : [])]);
  const lo = Math.min(...valores, TARIFA); const hi = Math.max(...valores, TARIFA);
  const esc = escalaBonita(lo - (hi - lo) * 0.05, hi + (hi - lo) * 0.05, 3);
  const slot = (W - M.izq - M.der) / xs.length;
  const cx = (i: number) => M.izq + slot * i + slot / 2;
  const sy = (v: number) => M.arriba + alto - ((v - esc.ini) / (esc.fin - esc.ini || 1)) * alto;
  const H = M.arriba + alto + M.abajo;
  const r = slot >= 10 ? 4 : 3.5;
  const hayFcl = xs.some((c) => c.limitado_por_peso && c.costo_m3_wm_fcl != null);
  const c = activo !== null ? xs[activo] : null;

  return (
    <div>
      <Leyenda items={[
        { nombre: "$/m³ real (volumétrico)", color: COLOR_VOL },
        ...(hayFcl ? [{ nombre: "W/M contenedor lleno (sólo llenos por peso)", color: COLOR_FCL }] : []),
        { nombre: `Tarifa del panel ${pesos(TARIFA)}`, color: SERIE.referencia, tipo: "guion" as const },
      ]} />
      <div ref={ref} className="relative mt-2 w-full select-none">
        {ancho > 0 && (
          <svg width="100%" viewBox={`0 0 ${W} ${H}`} role="img" tabIndex={0}
               aria-label={`$/m³ real de ${xs.length} contenedores, de ${pesos(xs[0].costo_m3)} a ${pesos(xs[xs.length - 1].costo_m3)}, contra la tarifa de ${pesos(TARIFA)}. Usa las flechas para recorrerlos.`}
               className="block rounded outline-none focus-visible:ring-2 focus-visible:ring-indigo-300"
               style={{ width: "100%", height: "auto" }}
               onKeyDown={(e) => {
                 if (e.key === "ArrowRight") { setActivo((a) => Math.min(xs.length - 1, (a ?? -1) + 1)); e.preventDefault(); }
                 if (e.key === "ArrowLeft") { setActivo((a) => Math.max(0, (a ?? xs.length) - 1)); e.preventDefault(); }
                 if (e.key === "Enter" && activo !== null && onElegir) onElegir(claveCont(xs[activo]));
                 if (e.key === "Escape") setActivo(null);
               }}
               onBlur={() => setActivo(null)} onPointerLeave={() => setActivo(null)}>
            {esc.marcas.map((m) => (
              <g key={m}>
                <line x1={M.izq} x2={W - M.der} y1={sy(m)} y2={sy(m)} stroke={SERIE.rejilla} strokeWidth={1} />
                <text x={M.izq - 6} y={sy(m) + 3.5} textAnchor="end" fontSize="10" className="fill-slate-500 tabular-nums">
                  {`$${(m / 1000).toFixed(m % 1000 ? 1 : 0)}k`}
                </text>
              </g>
            ))}
            <line x1={M.izq} x2={W - M.der} y1={sy(TARIFA)} y2={sy(TARIFA)} stroke={SERIE.referencia} strokeWidth={1.5} strokeDasharray="5 4" />
            <text x={M.izq + 4} y={sy(TARIFA) - 5} fontSize="10" fontWeight={600} className="fill-slate-500">tarifa del panel $7,500</text>
            {xs.map((k, i) => {
              const x = cx(i);
              const fcl = k.limitado_por_peso && k.costo_m3_wm_fcl != null ? k.costo_m3_wm_fcl : null;
              const tenue = activo !== null && activo !== i;
              return (
                <g key={claveCont(k)} opacity={tenue ? 0.45 : 1}>
                  {elegido === claveCont(k) && <rect x={x - slot / 2} y={M.arriba} width={slot} height={alto} fill="#EEF0FF" />}
                  {fcl != null && (
                    <>
                      <line x1={x} x2={x} y1={sy(k.costo_m3 as number)} y2={sy(fcl)} stroke={COLOR_FCL} strokeWidth={1.5} />
                      <circle cx={x} cy={sy(fcl)} r={r} fill={COLOR_FCL} stroke="#fff" strokeWidth={2} />
                    </>
                  )}
                  <circle cx={x} cy={sy(k.costo_m3 as number)} r={r} fill={COLOR_VOL} stroke="#fff" strokeWidth={2} />
                  {/* zona de pulso: la columna completa */}
                  <rect x={x - slot / 2} y={M.arriba} width={slot} height={alto} fill="transparent"
                        style={{ cursor: onElegir ? "pointer" : undefined }}
                        onPointerEnter={() => setActivo(i)} onPointerMove={() => setActivo(i)}
                        onClick={() => onElegir?.(claveCont(k))} />
                </g>
              );
            })}
            <line x1={M.izq} x2={W - M.der} y1={M.arriba + alto} y2={M.arriba + alto} stroke={SERIE.eje} strokeWidth={1} />
            <text x={M.izq} y={H - 6} fontSize="10" className="fill-slate-500">más barato por m³</text>
            <text x={W - M.der} y={H - 6} textAnchor="end" fontSize="10" className="fill-slate-500">más caro por m³ →</text>
          </svg>
        )}
        {c && activo !== null && ancho > 0 && (
          <div className="pointer-events-none absolute top-0 z-20 min-w-[190px] max-w-[260px] rounded-lg border border-slate-200 bg-white/95 px-3 py-2 text-[11.5px] shadow-lg backdrop-blur"
               style={cx(activo) > W * 0.58 ? { right: W - cx(activo) + 12 } : { left: cx(activo) + 12 }}>
            <div className="font-mono font-semibold text-slate-800">{c.codigo}</div>
            <div className="mt-1 flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: COLOR_VOL }} aria-hidden /><b className="tabular-nums text-slate-900">{pesos(Math.round(c.costo_m3 as number))}</b><span className="text-slate-500">/ m³ real</span></div>
            {c.limitado_por_peso && c.costo_m3_wm_fcl != null && (
              <div className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: COLOR_FCL }} aria-hidden /><b className="tabular-nums text-slate-900">{pesos(Math.round(c.costo_m3_wm_fcl))}</b><span className="text-slate-500">/ m³ W/M lleno</span></div>
            )}
            <div className="mt-1 text-slate-500">{cifra(c.total_cbm)} m³ · {entero(c.total_peso_kg)} kg ({pct(c.peso_vs_carga_util, 0)} de la carga útil)</div>
            <div className="text-slate-500">{nSkus(c)} SKU{nSkus(c) === 1 ? "" : "s"} de la muestra{c.rango_ok ? "" : " · fuera del rango de m³"}</div>
          </div>
        )}
      </div>
    </div>
  );
}
