"use client";

/**
 * SerieDias — cambios por día y por canal (17 días), misma escala en todos:
 * verde lo que llegó, rojo lo rechazado. Hoy va claro porque está a medias.
 */
import type { CanalEstado, Vivo } from "./tipos";

const PASO = 24;
const BARRA = 16;
const ALTO = 120;

function escala(max: number): { tope: number; marca: number } {
  const marca = max <= 40 ? 10 : max <= 100 ? 25 : max <= 250 ? 50 : max <= 600 ? 100 : 250;
  return { tope: Math.max(marca, Math.ceil(max / marca) * marca), marca };
}

export default function SerieDias({ serie, canales }: { serie: Vivo["serie"]; canales: CanalEstado[] }) {
  const n = serie.dias.length;
  const max = Math.max(1, ...serie.canales.flatMap((c) => c.ok.map((v, i) => v + c.mal[i])));
  const { tope, marca } = escala(max);
  const y = (v: number) => (v / tope) * ALTO;
  const marcas = Array.from({ length: Math.floor(tope / marca) }, (_, i) => (i + 1) * marca);
  const etiquetas = [0, Math.floor((n - 1) / 2), n - 3, n - 1].filter((v, i, a) => v >= 0 && a.indexOf(v) === i);

  return (
    <div className="grid gap-6" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(min(300px, 100%), 1fr))" }}>
      {serie.canales.map((c) => {
        const est = canales.find((x) => x.canal === c.canal);
        const llegaron = c.ok.reduce((a, b) => a + b, 0);
        const rech = c.mal.reduce((a, b) => a + b, 0);
        // Dónde empieza la racha de rechazos que sigue hasta hoy.
        let inicio = -1;
        if (est?.estado === "rechaza") {
          inicio = n - 1;
          while (inicio > 0 && c.ok[inicio - 1] === 0) inicio--;
          while (inicio < n - 1 && c.mal[inicio] === 0) inicio++;
        }
        return (
          <div key={c.canal} className="flex min-w-0 flex-col gap-2">
            <div className="flex items-baseline justify-between gap-2">
              <span className="text-sm font-bold text-slate-900">{c.nombre}</span>
              <span className="text-xs text-slate-600">
                {llegaron.toLocaleString("es-MX")} llegaron{rech ? ` · ${rech.toLocaleString("es-MX")} rechazados` : ""}
              </span>
            </div>
            <div className="relative" style={{ height: ALTO }}>
              <svg width="100%" height={ALTO} viewBox={`0 0 ${n * PASO} ${ALTO}`} preserveAspectRatio="none"
                role="img" aria-label={`${c.nombre}: ${llegaron} llegaron y ${rech} rechazados en ${n} días`} className="block">
                {marcas.map((m) => (
                  <line key={m} x1={0} x2={n * PASO} y1={ALTO - y(m)} y2={ALTO - y(m)} stroke="#e2e8f0" strokeWidth={1} vectorEffect="non-scaling-stroke" />
                ))}
                {c.ok.map((ok, i) => {
                  const mal = c.mal[i];
                  const x = i * PASO + (PASO - BARRA) / 2;
                  const op = i === n - 1 ? 0.45 : 1;
                  return (
                    <g key={i} opacity={op}>
                      {mal > 0 && <rect x={x} y={ALTO - y(ok + mal)} width={BARRA} height={y(mal)} fill="#f43f5e" />}
                      {ok > 0 && <rect x={x} y={ALTO - y(ok)} width={BARRA} height={y(ok)} fill="#059669" />}
                    </g>
                  );
                })}
                {inicio >= 0 && (
                  <line x1={inicio * PASO} x2={inicio * PASO} y1={0} y2={ALTO} stroke="#334155" strokeWidth={1}
                    strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
                )}
                <line x1={0} x2={n * PASO} y1={ALTO - 0.5} y2={ALTO - 0.5} stroke="#94a3b8" strokeWidth={1} vectorEffect="non-scaling-stroke" />
              </svg>
              {marcas.map((m) => (
                <span key={m} className="absolute left-0 bg-white/80 pr-1 text-[10px] leading-4 text-slate-500" style={{ top: ALTO - y(m) - 8 }}>{m}</span>
              ))}
              {inicio >= 0 && est?.racha_desde && (
                <span className="absolute top-0 whitespace-nowrap bg-white/85 px-1 text-[11px] font-semibold leading-4 text-slate-700"
                  style={inicio / n > 0.55 ? { right: `calc(${100 - (inicio / n) * 100}% + 4px)` } : { left: `calc(${(inicio / n) * 100}% + 4px)` }}>
                  {est.racha_desde} · empieza el rechazo
                </span>
              )}
            </div>
            <div className="relative h-4 text-[11px] leading-4 text-slate-600">
              {etiquetas.map((i) => {
                const centro = ((i + 0.5) / n) * 100;
                const estilo = i === 0 ? { left: 0 } : i === n - 1 ? { right: 0 } : { left: `${centro}%`, transform: "translateX(-50%)" };
                return <span key={i} className="absolute whitespace-nowrap" style={estilo}>{i === n - 1 ? "hoy" : serie.dias[i]}</span>;
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
