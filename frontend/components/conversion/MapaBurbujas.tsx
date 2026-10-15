"use client";

/**
 * Mapa de burbujas de Análisis › Conversión: una burbuja por publicación.
 *
 *   X = visitas en 30 días, Y = piezas vendidas en la MISMA ventana. Las dos
 *   escalas son logarítmicas: en lineal, las 10 publicaciones grandes aplastan
 *   a las otras 600 contra la esquina.
 *
 * En log-log una conversión fija es una DIAGONAL (piezas = c · visitas), así
 * que la meta se lee como «arriba o abajo de la línea índigo». Las de 0 piezas
 * o 0 visitas no tienen logaritmo: van en franjas grises al borde, con un
 * pequeño desplazamiento estable para que no se encimen.
 *
 * Es HTML con posiciones en %, no un <svg> con círculos: cada burbuja es un
 * <button> de verdad (teclado, lector de pantalla) y el contenedor es fluido.
 * El SVG solo dibuja las diagonales, estirado con `preserveAspectRatio="none"`.
 */

import { CUADRANTES, type Metricas, type PubConversion, type Reglas, num, pct } from "@/lib/conversion";

export interface FilaMapa { p: PubConversion; m: Metricas }

interface Props {
  filas: FilaMapa[];
  reglas: Reglas;
  seleccion: string | null;
  onSeleccion: (id: string | null) => void;
  tamano: "stock" | "ingreso" | "precio";
  colorPor: "cuadrante" | "cuenta";
}

const X0 = -0.5, Y0 = -0.5;
const COLOR_CUENTA: Record<string, string> = { BEKURA: "#4f46e5", SANCORFASHION: "#f59e0b" };

// Desplazamiento estable por publicación (no cambia entre renders).
function jitter(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
  return ((h >>> 0) % 1000) / 1000 - 0.5;
}

function radio(p: PubConversion, tamano: Props["tamano"]): number {
  if (tamano === "ingreso") return Math.min(30, 4 + Math.sqrt(p.ingreso || 0) / 25);
  if (tamano === "precio") return Math.min(26, 4 + Math.sqrt(p.precio || 0) / 3);
  return Math.min(30, 4 + Math.sqrt(Math.max(0, p.stock || 0)) * 0.9);
}

export default function MapaBurbujas({ filas, reglas, seleccion, onSeleccion, tamano, colorPor }: Props) {
  const medidas = filas.filter((f) => f.p.visitas != null && f.p.listing_id);
  const maxV = Math.max(10, ...medidas.map((f) => f.p.visitas || 0));
  const maxU = Math.max(10, ...medidas.map((f) => f.p.piezas));
  const X1 = Math.max(4.1, Math.log10(maxV) + 0.15);
  const Y1 = Math.max(2.6, Math.log10(maxU) + 0.2);
  const L = (x: number) => ((x - X0) / (X1 - X0)) * 100;
  const T = (y: number) => ((Y1 - y) / (Y1 - Y0)) * 100;

  // Diagonal de conversión c en el viewBox 0..1000 (x = L·10, y = T·10).
  const diagonal = (c: number) => {
    const lc = Math.log10(c);
    const xa = -lc, xb = Math.min(X1, Y1 - lc);
    return { x1: L(xa) * 10, y1: T(0) * 10, x2: L(xb) * 10, y2: T(xb + lc) * 10, fin: { x: L(xb), y: T(xb + lc) } };
  };
  const meta = diagonal(reglas.convMeta);
  const lq = L(Math.log10(Math.max(1, reglas.visitasAltas)));

  const burbujas = medidas.map(({ p, m }) => {
    const id = p.listing_id as string;
    const v = p.visitas || 0;
    const lx = v > 0 ? Math.log10(v) : -0.3 + jitter(id) * 0.18;
    const ly = p.piezas > 0 ? Math.log10(p.piezas) : -0.3 + jitter(id + "y") * 0.22;
    const r = radio(p, tamano);
    const hueca = m.cuadrante === "chica" && colorPor === "cuadrante";
    const color = colorPor === "cuenta" ? (COLOR_CUENTA[p.cuenta] ?? "#64748b") : CUADRANTES[m.cuadrante].color;
    return { id, p, m, r, lx, ly, hueca, color };
  }).sort((a, b) => (a.id === seleccion ? 1 : b.id === seleccion ? -1 : b.r - a.r));

  const ticksX = Array.from({ length: Math.floor(X1) + 1 }, (_, k) => k);
  const ticksY = Array.from({ length: Math.floor(Y1) + 1 }, (_, k) => k);
  const zonaX = L(-0.1), zonaY = T(-0.1);

  return (
    <div className="relative" style={{ height: 540, margin: "20px 8px 48px 56px" }}>
      {/* franjas de los ceros */}
      <div className="absolute left-0 right-0 bottom-0 rounded bg-slate-100" style={{ top: `${zonaY}%` }} />
      <div className="absolute left-0 top-0 bottom-0 rounded bg-slate-100" style={{ width: `${zonaX}%` }} />
      {ticksX.map((k) => (
        <div key={`gx${k}`} className="absolute top-0 bottom-0 border-l border-slate-200" style={{ left: `${L(k)}%` }} />
      ))}
      {ticksY.map((k) => (
        <div key={`gy${k}`} className="absolute left-0 right-0 border-t border-slate-200" style={{ top: `${T(k)}%` }} />
      ))}

      <svg aria-hidden="true" viewBox="0 0 1000 1000" preserveAspectRatio="none"
        className="absolute inset-0 h-full w-full overflow-visible">
        {[0.01, 0.1].map((c) => {
          const d = diagonal(c);
          return <line key={c} x1={d.x1} y1={d.y1} x2={d.x2} y2={d.y2} stroke="#cbd5e1"
            strokeDasharray="4 4" vectorEffect="non-scaling-stroke" />;
        })}
        <line x1={meta.x1} y1={meta.y1} x2={meta.x2} y2={meta.y2} stroke="#4f46e5" strokeWidth={1.5}
          strokeDasharray="7 5" vectorEffect="non-scaling-stroke" />
      </svg>
      <span className="absolute rounded bg-white px-1 text-[11px] font-semibold text-indigo-700"
        style={meta.fin.y < 3
          ? { top: 4, left: `calc(${meta.fin.x}% - 82px)` }
          : { right: 4, top: `calc(${meta.fin.y}% + 6px)` }}>
        meta {pct(reglas.convMeta)}
      </span>

      {/* corte de «mucho tráfico» */}
      <div className="absolute top-0 border-l-[1.5px] border-dashed border-slate-400"
        style={{ left: `${lq}%`, bottom: `${100 - zonaY}%` }} />
      <span className="absolute text-[11px] text-slate-600" style={{ left: `calc(${lq}% + 6px)`, top: `calc(${zonaY}% + 4px)` }}>
        {num(reglas.visitasAltas)} visitas
      </span>

      <span className="absolute text-xs font-semibold text-teal-700" style={{ left: `calc(${zonaX}% + 8px)`, top: 6 }}>Joyas ocultas</span>
      <span className="absolute text-xs font-semibold text-indigo-700" style={{ left: `calc(${lq}% + 8px)`, top: 6 }}>Ganadoras</span>
      <span className="absolute right-2 text-xs font-semibold text-orange-700" style={{ top: `calc(${zonaY}% - 24px)` }}>Tráfico sin venta</span>
      <span className="absolute text-xs font-semibold text-slate-500" style={{ left: `calc(${zonaX}% + 8px)`, top: `calc(${zonaY}% - 24px)` }}>Dormidas</span>

      {burbujas.map((b) => {
        const sel = b.id === seleccion;
        const etiqueta = `${b.p.titulo ?? b.p.sku} (${b.p.sku}, ${b.p.cuenta}): ${num(b.p.visitas)} visitas, `
          + `${num(b.p.piezas)} piezas, conversión ${pct(b.m.conv)}`;
        return (
          <button key={b.id} type="button" title={etiqueta} aria-label={etiqueta} aria-pressed={sel}
            onClick={() => onSeleccion(sel ? null : b.id)}
            className="absolute rounded-full p-0 transition-shadow focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600"
            style={{
              left: `${L(b.lx)}%`, top: `${T(b.ly)}%`, width: 2 * b.r, height: 2 * b.r,
              marginLeft: -b.r, marginTop: -b.r,
              background: b.hueca ? "rgba(255,255,255,.75)" : `${b.color}cc`,
              border: b.hueca ? "1.5px dashed #64748b" : "1.5px solid #ffffff",
              boxShadow: sel ? "0 0 0 3px #0f172a" : "0 1px 2px rgba(15,23,42,.2)",
              zIndex: sel ? 5 : undefined,
            }} />
        );
      })}

      {ticksY.map((k) => (
        <span key={`ty${k}`} className="absolute w-11 text-right text-[11px] text-slate-500"
          style={{ left: -52, top: `calc(${T(k)}% - 8px)` }}>{num(10 ** k)}</span>
      ))}
      <span className="absolute w-11 text-right text-[11px] text-slate-500" style={{ left: -52, top: `calc(${T(-0.3)}% - 8px)` }}>0</span>
      {ticksX.map((k) => (
        <span key={`tx${k}`} className="absolute w-14 text-center text-[11px] text-slate-500"
          style={{ left: `calc(${L(k)}% - 28px)`, top: "calc(100% + 8px)" }}>{num(10 ** k)}</span>
      ))}
      <span className="absolute w-10 text-center text-[11px] text-slate-500" style={{ left: `calc(${L(-0.3)}% - 20px)`, top: "calc(100% + 8px)" }}>0</span>
      <span className="absolute inset-x-0 text-center text-xs font-semibold text-slate-700" style={{ top: "calc(100% + 26px)" }}>
        Visitas en 30 días (escala logarítmica)
      </span>
      <span className="absolute text-xs font-semibold text-slate-700" style={{ left: -52, top: -20 }}>Piezas vendidas</span>
    </div>
  );
}
