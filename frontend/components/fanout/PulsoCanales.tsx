"use client";

/**
 * PulsoCanales — el ritmo de cada canal en el veredicto (reemplaza al pastel;
 * opción C del lienzo). Una fila por canal: cambios por hora en las últimas
 * 24 h (verde lo que llegó, rojo lo rechazado, misma escala en todos), el total
 * del día y su estado.
 *
 * El punto de cada canal LATE solo si tuvo escrituras en las últimas dos horas:
 * un latido es actividad de verdad, no adorno. La serie viene de
 * `fanout_vivo._pulso_24h`, con la misma ventana que `ok_24h`, así que la suma de
 * las barras es la cifra que muestran las tarjetas de la cadena.
 */
import type { CanalEstado, Vivo } from "./tipos";
import { ESTADO_CANAL } from "./tipos";

const PASO = 10;
const BARRA = 7;
const ALTO = 32;
const COLUMNAS = "minmax(84px, 150px) minmax(96px, 240px) minmax(84px, 1fr)";

const PUNTO: Record<CanalEstado["estado"], string> = {
  al_dia: "#059669",
  con_errores: "#d97706",
  rechaza: "#e11d48",
  sin_cambios: "#94a3b8",
};

export default function PulsoCanales({ pulso, canales }: { pulso: Vivo["pulso"]; canales: CanalEstado[] }) {
  const max = Math.max(1, ...pulso.flatMap((p) => p.ok.map((v, i) => v + p.mal[i])));
  const alto = (v: number) => (v > 0 ? Math.max(1.5, (v / max) * (ALTO - 2)) : 0);
  const horas = pulso[0]?.ok.length ?? 24;

  return (
    <div className="flex min-w-0 flex-[0_1_640px] flex-col gap-2.5">
      <style>{`
        @keyframes fo-latido-ok { 0% { box-shadow: 0 0 0 0 rgba(5,150,105,.55) } 70% { box-shadow: 0 0 0 7px rgba(5,150,105,0) } 100% { box-shadow: 0 0 0 0 rgba(5,150,105,0) } }
        @keyframes fo-latido-mal { 0% { box-shadow: 0 0 0 0 rgba(225,29,72,.55) } 70% { box-shadow: 0 0 0 7px rgba(225,29,72,0) } 100% { box-shadow: 0 0 0 0 rgba(225,29,72,0) } }
        .fo-latido-ok { animation: fo-latido-ok 1.6s ease-out infinite }
        .fo-latido-mal { animation: fo-latido-mal 1.6s ease-out infinite }
        @media (prefers-reduced-motion: reduce) { .fo-latido-ok, .fo-latido-mal { animation: none } }
      `}</style>
      <div className="grid gap-2.5 text-[11px] font-semibold uppercase leading-4 tracking-wide text-slate-500 sm:gap-3.5"
        style={{ gridTemplateColumns: COLUMNAS }}>
        <span>Canal</span><span>Cambios por hora · 24 h</span><span>Hoy</span>
      </div>
      {pulso.map((p) => {
        const c = canales.find((x) => x.canal === p.canal);
        const estado = c?.estado ?? "sin_cambios";
        const llegaron = p.ok.reduce((a, b) => a + b, 0);
        const rechazados = p.mal.reduce((a, b) => a + b, 0);
        const reciente = p.ok.slice(-2).some(Boolean) || p.mal.slice(-2).some(Boolean);
        const latido = reciente ? (estado === "rechaza" ? "fo-latido-mal" : "fo-latido-ok") : "";
        return (
          <div key={p.canal} className="grid items-center gap-2.5 sm:gap-3.5" style={{ gridTemplateColumns: COLUMNAS }}>
            <span className="flex min-w-0 items-center gap-2.5 text-[15px] font-semibold text-slate-900">
              <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${latido}`} style={{ background: PUNTO[estado] }} />
              <span className="leading-5">{p.nombre}</span>
            </span>
            <svg width="100%" height={ALTO} viewBox={`0 0 ${horas * PASO} ${ALTO}`} preserveAspectRatio="none" className="block"
              role="img" aria-label={`${p.nombre}: ${llegaron} llegaron y ${rechazados} rechazados en las últimas 24 horas`}>
              {p.ok.map((ok, i) => {
                const mal = p.mal[i];
                const x = i * PASO + (PASO - BARRA) / 2;
                if (!ok && !mal) return <rect key={i} x={x} y={ALTO - 2} width={BARRA} height={1} fill="#cbd5e1" />;
                const total = alto(ok + mal);
                const verde = ok ? alto(ok) : 0;
                const rojo = mal ? Math.max(1.5, total - verde) : 0;
                return (
                  <g key={i}>
                    {rojo > 0 && <rect x={x} y={ALTO - 1 - verde - rojo} width={BARRA} height={rojo} fill="#e11d48" />}
                    {verde > 0 && <rect x={x} y={ALTO - 1 - verde} width={BARRA} height={verde} fill="#059669" />}
                  </g>
                );
              })}
            </svg>
            <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-slate-700">
              <b className="text-lg leading-6 text-slate-900">{llegaron.toLocaleString("es-MX")}</b>llegaron
              {rechazados > 0 && <span className="text-rose-800">· {rechazados.toLocaleString("es-MX")} rechazados</span>}
              <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${ESTADO_CANAL[estado].chip}`}>{ESTADO_CANAL[estado].texto}</span>
            </span>
          </div>
        );
      })}
      <div className="grid gap-2.5 text-[11px] leading-4 text-slate-500 sm:gap-3.5" style={{ gridTemplateColumns: COLUMNAS }}>
        <span />
        <span className="flex justify-between"><span>hace 24 h</span><span>ahora</span></span>
        <span />
      </div>
    </div>
  );
}
