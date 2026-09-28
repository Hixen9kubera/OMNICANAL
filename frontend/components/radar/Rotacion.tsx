"use client";

// Rotacion.tsx — Unidades vendidas por día en los últimos 90 días.
//
// La serie viene del backend (`channel.sales_daily_completa`). Un día que no
// aparece en ella es un día SIN venta —la tabla sólo guarda días con venta—,
// por eso ahí sí se pinta 0. La línea es el ritmo diario que calculó el
// backend (`ventas_dia`), no un promedio hecho aquí.

import type { RadarDiaVenta } from "@/lib/api";
import { decimal, entero } from "./formato";

const DIAS = 90;
const ANCHO = 540;
const ALTO = 112;
const IZQ = 26;
const DER = 8;
const ARRIBA = 14;
const PISO_Y = 82;

function claveDia(d: Date): string {
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}

/** Los 90 días que terminan en `hasta` (incluido), con su venta o 0. */
export function diasDeSerie(serie: RadarDiaVenta[], hasta: string | null | undefined): { fecha: string; unidades: number }[] {
  const porDia = new Map<string, number>();
  for (const p of serie ?? []) {
    if (!p?.fecha) continue;
    const k = p.fecha.slice(0, 10);
    porDia.set(k, (porDia.get(k) ?? 0) + (Number.isFinite(p.unidades) ? p.unidades : 0));
  }
  // El último día es el más reciente de la serie (el backend la manda completa,
  // en fechas de México); sin serie, el día de la foto; si no, hoy.
  const ultimo = [...porDia.keys()].sort().pop();
  let fin = ultimo ? new Date(`${ultimo}T00:00:00Z`) : hasta ? new Date(hasta) : null;
  if (!fin || Number.isNaN(fin.getTime())) fin = new Date();
  const base = Date.UTC(fin.getUTCFullYear(), fin.getUTCMonth(), fin.getUTCDate());
  const out: { fecha: string; unidades: number }[] = [];
  for (let i = DIAS - 1; i >= 0; i--) {
    const k = claveDia(new Date(base - i * 86_400_000));
    out.push({ fecha: k, unidades: porDia.get(k) ?? 0 });
  }
  return out;
}

export default function Rotacion({
  serie,
  hasta,
  ventasDia,
}: {
  serie: RadarDiaVenta[];
  hasta: string | null | undefined;
  ventasDia: number | null;
}) {
  const dias = diasDeSerie(serie, hasta);
  const maximo = Math.max(1, ...dias.map((d) => d.unidades));
  const tope = maximo <= 3 ? 3 : Math.ceil(maximo);
  const anchoPlot = ANCHO - IZQ - DER;
  const paso = anchoPlot / DIAS;
  const y = (u: number) => PISO_Y - (u / tope) * (PISO_Y - ARRIBA);
  const x30 = IZQ + paso * (DIAS - 30);
  const total = dias.reduce((s, d) => s + d.unidades, 0);
  const yRitmo = ventasDia !== null && Number.isFinite(ventasDia) ? y(Math.min(ventasDia, tope)) : null;
  const guias = [tope, tope / 3 * 2, tope / 3].map((v) => Math.round(v * 10) / 10);

  return (
    <div>
      <svg
        viewBox={`0 0 ${ANCHO} ${ALTO}`}
        className="h-auto w-full"
        role="img"
        aria-label={`Unidades vendidas por día en los últimos 90 días: ${entero(total)} en total, máximo ${entero(maximo)} en un día${
          ventasDia !== null ? `, ritmo de ${decimal(ventasDia)} por día` : ""
        }.`}
      >
        <rect x={x30} y={ARRIBA - 2} width={IZQ + anchoPlot - x30} height={PISO_Y - ARRIBA + 2} fill="#F6F7FB" />
        {guias.map((g) => (
          <g key={g}>
            <path d={`M${IZQ} ${y(g)}H${IZQ + anchoPlot}`} stroke="#EEF0F4" strokeWidth={1} />
            <text x={IZQ - 6} y={y(g) + 4} textAnchor="end" fontSize={11} fill="#667085">{decimal(g)}</text>
          </g>
        ))}
        <text x={IZQ - 6} y={PISO_Y + 4} textAnchor="end" fontSize={11} fill="#667085">0</text>
        <g fill="#C4C9D6">
          {dias.map((d, i) =>
            d.unidades > 0 ? (
              <rect
                key={d.fecha}
                x={IZQ + i * paso + 0.6}
                y={y(d.unidades)}
                width={Math.max(1, paso - 1.2)}
                height={PISO_Y - y(d.unidades)}
              >
                <title>{`${d.fecha}: ${d.unidades} u.`}</title>
              </rect>
            ) : null,
          )}
        </g>
        <path d={`M${IZQ} ${PISO_Y}H${IZQ + anchoPlot}`} stroke="#D0D5DD" strokeWidth={1} />
        {yRitmo !== null && (
          <path d={`M${IZQ} ${yRitmo}H${IZQ + anchoPlot}`} stroke="#4F46E5" strokeWidth={2} strokeDasharray="6 4" />
        )}
        <path d={`M${x30} ${ARRIBA - 2}V${PISO_Y}`} stroke="#98A2B3" strokeWidth={1} strokeDasharray="3 3" />
        <text x={x30 + 4} y={ARRIBA - 4} fontSize={11} fill="#4A5163">últimos 30 días</text>
        <g fontSize={11} fill="#667085">
          <text x={IZQ} y={ALTO - 8} textAnchor="start">hace 90 días</text>
          <text x={x30} y={ALTO - 8} textAnchor="middle">hace 30 días</text>
          <text x={IZQ + anchoPlot} y={ALTO - 8} textAnchor="end">hoy</text>
        </g>
      </svg>
      <div className="mt-1 flex flex-wrap gap-x-5 gap-y-1 text-xs text-[#4A5163]">
        <span className="inline-flex items-center gap-1.5">
          <svg width={10} height={12} viewBox="0 0 10 12" aria-hidden><rect x={2} y={0} width={6} height={12} fill="#C4C9D6" /></svg>
          Unidades por día
        </span>
        <span className="inline-flex items-center gap-1.5">
          <svg width={18} height={10} viewBox="0 0 18 10" aria-hidden>
            <path d="M1 5H17" stroke="#4F46E5" strokeWidth={2} strokeDasharray="6 4" />
          </svg>
          Ritmo diario del radar
        </span>
      </div>
    </div>
  );
}
