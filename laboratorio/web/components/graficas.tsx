"use client";

/**
 * Gráficas del laboratorio: SVG a mano, como las del panel (no hay librería de
 * gráficas en el frontend y no se agrega una por esto).
 *
 * Reglas de la casa de dataviz que se respetan aquí:
 *   · UN eje Y por gráfica. Magnitudes distintas (utilidad $/día, unidades,
 *     visitas, conversión) van en paneles pequeños apilados que comparten el eje
 *     X y el mismo cursor — nunca en un doble eje, que inventa correlaciones.
 *   · Líneas de 2 px, barras de ≤ 24 px con 4 px redondeados sólo en la punta,
 *     2 px de hueco entre segmentos, rejilla en hairline sólida y recesiva.
 *   · Capa de cursor por omisión: línea vertical que se pega al dato más cercano
 *     y un solo tooltip con TODAS las series de ese X (también con flechas del
 *     teclado). El tooltip nunca es la única vía: cada gráfica trae su tabla.
 *   · El texto va en tinta (slate), nunca en el color de la serie.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { SERIE } from "@/lib/tema";

// ── utilidades ──────────────────────────────────────────────────────────────
export function useAncho<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [ancho, setAncho] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const medir = () => setAncho(Math.floor(el.getBoundingClientRect().width));
    medir();
    // Segunda medida cuando el marco ya se asentó (la rejilla de la página cambia de
    // columnas al cargar los datos); el ResizeObserver cubre el resto.
    const t = window.setTimeout(medir, 120);
    const ro = new ResizeObserver(medir);
    ro.observe(el);
    window.addEventListener("resize", medir);
    return () => { ro.disconnect(); window.clearTimeout(t); window.removeEventListener("resize", medir); };
  }, []);
  return [ref, ancho];
}

function pasoBonito(x: number): number {
  if (!(x > 0)) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(x)));
  const f = x / e;
  return (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * e;
}

/** Dominio redondeado a pasos limpios y sus marcas (0 / 1,000 / 2,000…). */
export function escalaBonita(min: number, max: number, n = 3): { ini: number; fin: number; marcas: number[] } {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return { ini: 0, fin: 1, marcas: [0, 1] };
  if (max === min) { max = min === 0 ? 1 : min + Math.abs(min) * 0.5; }
  const paso = pasoBonito((max - min) / n);
  const ini = Math.floor(min / paso) * paso;
  const fin = Math.ceil(max / paso) * paso;
  const marcas: number[] = [];
  for (let v = ini; v <= fin + paso / 2; v += paso) marcas.push(Math.round(v / paso) * paso);
  return { ini, fin, marcas };
}

/** Índice del valor más cercano en una lista ORDENADA. */
function masCercano(xs: number[], x: number): number {
  let lo = 0, hi = xs.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (xs[m] < x) lo = m; else hi = m; }
  return Math.abs(xs[lo] - x) <= Math.abs(xs[hi] - x) ? lo : hi;
}

function trazo(xs: number[], ys: (number | null)[], sx: (v: number) => number, sy: (v: number) => number): string {
  let d = ""; let pluma = false;
  for (let i = 0; i < xs.length; i++) {
    const y = ys[i];
    if (y === null || y === undefined || !Number.isFinite(y)) { pluma = false; continue; }
    d += `${pluma ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(y).toFixed(1)}`;
    pluma = true;
  }
  return d;
}

/** Barra con 4 px redondeados sólo arriba (la base, cuadrada, pega con la línea base). */
function barraRedonda(x: number, y: number, w: number, h: number, r = 4, redonda = true): string {
  if (h <= 0 || w <= 0) return "";
  const rr = redonda ? Math.min(r, h, w / 2) : 0;
  return `M${x},${y + h}L${x},${y + rr}Q${x},${y} ${x + rr},${y}L${x + w - rr},${y}Q${x + w},${y} ${x + w},${y + rr}L${x + w},${y + h}Z`;
}

function Tooltip({ x, ancho, children }: { x: number; ancho: number; children: ReactNode }) {
  const derecha = x > ancho * 0.58;
  return (
    <div className="pointer-events-none absolute top-2 z-20 min-w-[168px] max-w-[260px] rounded-lg border border-slate-200 bg-white/95 px-3 py-2 text-[11.5px] shadow-lg backdrop-blur"
         style={derecha ? { right: ancho - x + 12 } : { left: x + 12 }}>
      {children}
    </div>
  );
}

function RenglonTooltip({ color, nombre, valor, guion }: { color: string; nombre: string; valor: string; guion?: boolean }) {
  return (
    <div className="flex items-center gap-2 py-[1px]">
      <svg width="14" height="6" aria-hidden className="shrink-0">
        <line x1="1" y1="3" x2="13" y2="3" stroke={color} strokeWidth="2" strokeLinecap="round" strokeDasharray={guion ? "3 3" : undefined} />
      </svg>
      <span className="font-semibold tabular-nums text-slate-900">{valor}</span>
      <span className="truncate text-slate-500">{nombre}</span>
    </div>
  );
}

// ── Paneles pequeños con eje X compartido ───────────────────────────────────
export interface SerieX {
  nombre: string;
  color: string;
  valores: (number | null)[];
  formato: (v: number) => string;
  guion?: boolean;
  area?: boolean;
  /** Marcas de ≥ 8 px en cada punto con dato (series ralas, p. ej. el recomendado por snapshot). */
  puntos?: boolean;
}
export interface PanelX {
  titulo: string;
  formatoEje: (v: number) => string;
  series: SerieX[];
  alto?: number;
  /** false = el eje Y arranca cerca del mínimo (precios). Por omisión incluye el cero. */
  incluirCero?: boolean;
  /** Punto destacado (p. ej. el máximo de utilidad). */
  destacar?: { indice: number; etiqueta: string } | null;
}
export interface MarcadorX { x: number; corto: string; etiqueta: string; color: string; estilo?: "solido" | "guion" | "punto"; grosor?: number }
export interface ZonaX { desde: number; hasta: number; color: string; etiqueta: string }

export function PanelesX({ xs, paneles, formatoX, formatoXLargo, marcadores = [], zonas = [], xEsIndice = false }: {
  xs: number[];
  paneles: PanelX[];
  formatoX: (x: number) => string;
  formatoXLargo?: (x: number) => string;
  marcadores?: MarcadorX[];
  zonas?: ZonaX[];
  xEsIndice?: boolean;
}) {
  const [ref, ancho] = useAncho<HTMLDivElement>();
  const [cursor, setCursor] = useState<number | null>(null);
  const M = { izq: 52, der: 14, arriba: 22, entre: 30, abajo: 26 };
  const hayMarcas = marcadores.length > 0;
  const arriba = M.arriba + (hayMarcas ? 16 : 0);
  const alturas = paneles.map((p) => p.alto ?? 92);
  const altoTotal = arriba + alturas.reduce((a, b) => a + b, 0) + M.entre * (paneles.length - 1) + M.abajo;
  const W = Math.max(ancho, 280);
  const xMin = xs.length ? xs[0] : 0;
  const xMax = xs.length ? xs[xs.length - 1] : 1;
  const sx = (v: number) => M.izq + ((v - xMin) / (xMax - xMin || 1)) * (W - M.izq - M.der);

  const geo = useMemo(() => {
    let y0 = arriba;
    return paneles.map((p, i) => {
      const vals = p.series.flatMap((s) => s.valores).filter((v): v is number => v !== null && Number.isFinite(v));
      const bruto = vals.length ? Math.min(...vals) : 0;
      const max = Math.max(...(vals.length ? vals : [1]));
      // Una línea de precio no necesita el cero: pegarla al cero la aplana y esconde los cambios.
      const min = p.incluirCero === false ? Math.max(0, bruto - (max - bruto) * 0.15) : Math.min(0, bruto);
      const esc = escalaBonita(min, max, alturas[i] < 80 ? 2 : 3);
      const top = y0;
      y0 += alturas[i] + M.entre;
      const sy = (v: number) => top + alturas[i] - ((v - esc.ini) / (esc.fin - esc.ini || 1)) * alturas[i];
      return { top, alto: alturas[i], esc, sy };
    });
  }, [paneles, alturas, arriba, M.entre]);

  const marcasX = useMemo(() => {
    if (!xs.length) return [];
    if (xEsIndice) {
      const n = Math.max(2, Math.min(7, Math.floor((W - M.izq) / 90)));
      return Array.from({ length: n }, (_, k) => Math.round(xMin + ((xMax - xMin) * k) / (n - 1)));
    }
    return escalaBonita(xMin, xMax, Math.max(3, Math.min(8, Math.floor((W - M.izq) / 80)))).marcas.filter((v) => v >= xMin && v <= xMax);
  }, [xs, xEsIndice, W, xMin, xMax, M.izq]);

  // Etiquetas de marcadores: si dos quedan a < 18 px se escalonan (no se enciman).
  const etiquetas = useMemo(() => {
    const orden = marcadores.filter((m) => m.x >= xMin && m.x <= xMax).map((m) => ({ ...m, px: sx(m.x), nivel: 0 })).sort((a, b) => a.px - b.px);
    for (let i = 1; i < orden.length; i++) {
      if (orden[i].px - orden[i - 1].px < 18) orden[i].nivel = orden[i - 1].nivel === 0 ? 1 : 0;
    }
    return orden;
  }, [marcadores, xMin, xMax, W]); // eslint-disable-line react-hooks/exhaustive-deps

  const fondo = altoTotal - M.abajo;
  const moverA = (clientX: number, rect: DOMRect) => {
    if (!xs.length) return;
    const px = ((clientX - rect.left) / rect.width) * W;
    const x = xMin + ((px - M.izq) / (W - M.izq - M.der)) * (xMax - xMin);
    setCursor(masCercano(xs, x));
  };

  return (
    <div ref={ref} className="relative w-full select-none">
      {ancho > 0 && (
        <svg width="100%" viewBox={`0 0 ${W} ${altoTotal}`} role="img" tabIndex={0}
             aria-label={`Gráfica: ${paneles.map((p) => p.titulo).join(", ")}. Usa las flechas para recorrerla.`}
             className="block outline-none focus-visible:ring-2 focus-visible:ring-indigo-300 rounded"
             style={{ width: "100%", height: "auto" }}
             onPointerMove={(e) => moverA(e.clientX, (e.currentTarget as SVGSVGElement).getBoundingClientRect())}
             onPointerDown={(e) => moverA(e.clientX, (e.currentTarget as SVGSVGElement).getBoundingClientRect())}
             onPointerLeave={() => setCursor(null)}
             onKeyDown={(e) => {
               if (e.key === "ArrowRight") { setCursor((c) => Math.min(xs.length - 1, (c ?? -1) + 1)); e.preventDefault(); }
               if (e.key === "ArrowLeft") { setCursor((c) => Math.max(0, (c ?? xs.length) - 1)); e.preventDefault(); }
               if (e.key === "Escape") setCursor(null);
             }}
             onBlur={() => setCursor(null)}>
          {/* zonas (p. ej. «pierde dinero») detrás de todo */}
          {zonas.map((z) => {
            const a = sx(Math.max(xMin, z.desde)); const b = sx(Math.min(xMax, z.hasta));
            if (b <= a) return null;
            return <rect key={z.etiqueta} x={a} y={arriba} width={b - a} height={fondo - arriba} fill={z.color} opacity={0.07} />;
          })}
          {paneles.map((p, i) => {
            const g = geo[i];
            return (
              <g key={p.titulo}>
                <text x={M.izq} y={g.top - 8} className="fill-slate-600" fontSize="11" fontWeight={600}>{p.titulo}</text>
                {g.esc.marcas.map((m) => (
                  <g key={m}>
                    <line x1={M.izq} x2={W - M.der} y1={g.sy(m)} y2={g.sy(m)} stroke={m === 0 && g.esc.ini < 0 ? SERIE.eje : SERIE.rejilla} strokeWidth={1} />
                    <text x={M.izq - 6} y={g.sy(m) + 3.5} textAnchor="end" fontSize="10" className="fill-slate-500 tabular-nums">{p.formatoEje(m)}</text>
                  </g>
                ))}
                {p.series.map((s) => {
                  const d = trazo(xs, s.valores, sx, g.sy);
                  if (!d) return null;
                  return (
                    <g key={s.nombre}>
                      {s.area && (
                        <path d={`${d}L${sx(xs[xs.length - 1]).toFixed(1)},${g.sy(Math.max(g.esc.ini, 0))}L${sx(xs[0]).toFixed(1)},${g.sy(Math.max(g.esc.ini, 0))}Z`}
                              fill={s.color} opacity={0.1} />
                      )}
                      <path d={d} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round"
                            strokeDasharray={s.guion ? "5 4" : undefined} />
                      {s.puntos && s.valores.map((v, k) => (v === null || v === undefined ? null : (
                        <circle key={k} cx={sx(xs[k])} cy={g.sy(v)} r={4} fill={s.color} stroke="#fff" strokeWidth={2} />
                      )))}
                    </g>
                  );
                })}
                {p.destacar && p.series[0] && p.series[0].valores[p.destacar.indice] != null && (() => {
                  const v = p.series[0].valores[p.destacar.indice] as number;
                  const cx = sx(xs[p.destacar.indice]); const cy = g.sy(v);
                  return (
                    <g>
                      <circle cx={cx} cy={cy} r={4.5} fill={p.series[0].color} stroke="#fff" strokeWidth={2} />
                      {/* Cerca de una orilla, la etiqueta se ancla hacia adentro para no cortarse. */}
                      <text x={cx > W - 70 ? cx - 7 : cx < M.izq + 40 ? cx + 7 : cx} y={cy - 9}
                            textAnchor={cx > W - 70 ? "end" : cx < M.izq + 40 ? "start" : "middle"}
                            fontSize="10" fontWeight={600} className="fill-slate-600">{p.destacar.etiqueta}</text>
                    </g>
                  );
                })()}
              </g>
            );
          })}
          {/* eje X */}
          <line x1={M.izq} x2={W - M.der} y1={fondo} y2={fondo} stroke={SERIE.eje} strokeWidth={1} />
          {marcasX.map((m) => (
            <text key={m} x={sx(m)} y={fondo + 15} textAnchor="middle" fontSize="10" className="fill-slate-500 tabular-nums">{formatoX(m)}</text>
          ))}
          {/* marcadores verticales con su letra arriba */}
          {etiquetas.map((m) => (
            <g key={m.corto}>
              <line x1={m.px} x2={m.px} y1={arriba - 2} y2={fondo} stroke={m.color} strokeWidth={m.grosor ?? 1.5}
                    strokeDasharray={m.estilo === "guion" ? "4 3" : m.estilo === "punto" ? "1.5 3" : undefined} opacity={0.9} />
              <g transform={`translate(${m.px},${M.arriba - 12 + m.nivel * 14})`}>
                <rect x={-8} y={-7} width={16} height={14} rx={4} fill={m.color} />
                <text x={0} y={3.5} textAnchor="middle" fontSize="9.5" fontWeight={700} fill="#fff">{m.corto}</text>
                <title>{m.etiqueta}</title>
              </g>
            </g>
          ))}
          {/* cursor */}
          {cursor !== null && xs[cursor] !== undefined && (
            <g pointerEvents="none">
              <line x1={sx(xs[cursor])} x2={sx(xs[cursor])} y1={arriba} y2={fondo} stroke="#94a3b8" strokeWidth={1} />
              {paneles.map((p, i) => p.series.map((s) => {
                const v = s.valores[cursor];
                if (v === null || v === undefined) return null;
                return <circle key={`${p.titulo}-${s.nombre}`} cx={sx(xs[cursor])} cy={geo[i].sy(v)} r={4} fill={s.color} stroke="#fff" strokeWidth={2} />;
              }))}
            </g>
          )}
        </svg>
      )}
      {cursor !== null && xs[cursor] !== undefined && ancho > 0 && (
        <Tooltip x={sx(xs[cursor])} ancho={W}>
          <div className="mb-1 font-semibold text-slate-700">{(formatoXLargo ?? formatoX)(xs[cursor])}</div>
          {paneles.map((p) => p.series.map((s) => {
            const v = s.valores[cursor];
            return <RenglonTooltip key={`${p.titulo}-${s.nombre}`} color={s.color} nombre={s.nombre} guion={s.guion}
                                   valor={v === null || v === undefined ? "sin dato" : s.formato(v)} />;
          }))}
        </Tooltip>
      )}
    </div>
  );
}

// ── Columnas (apiladas o sencillas) ─────────────────────────────────────────
export interface SerieBarras { nombre: string; color: string; valores: (number | null)[] }

export function Columnas({ etiquetas, series, formato, formatoEje, alto = 170, etiquetaLarga, cadaCuanto, destacarIndice }: {
  etiquetas: string[];
  series: SerieBarras[];
  formato: (v: number) => string;
  formatoEje: (v: number) => string;
  alto?: number;
  etiquetaLarga?: (i: number) => string;
  /** Cada cuántas columnas va una etiqueta en el eje X (se calcula si falta). */
  cadaCuanto?: number;
  destacarIndice?: number | null;
}) {
  const [ref, ancho] = useAncho<HTMLDivElement>();
  const [activo, setActivo] = useState<number | null>(null);
  const M = { izq: 52, der: 8, arriba: 10, abajo: 24 };
  const W = Math.max(ancho, 260);
  const n = etiquetas.length;
  const totales = etiquetas.map((_, i) => series.reduce((a, s) => a + Math.max(0, s.valores[i] ?? 0), 0));
  const esc = escalaBonita(0, Math.max(1, ...totales), 3);
  const slot = (W - M.izq - M.der) / Math.max(1, n);
  const bw = Math.max(2, Math.min(24, slot - 2));
  const sy = (v: number) => M.arriba + alto - (v / (esc.fin || 1)) * alto;
  const cada = cadaCuanto ?? Math.max(1, Math.ceil(n / Math.max(2, Math.floor((W - M.izq) / 64))));
  const H = M.arriba + alto + M.abajo;

  return (
    <div ref={ref} className="relative w-full select-none">
      {ancho > 0 && (
        <svg width="100%" viewBox={`0 0 ${W} ${H}`} role="img" tabIndex={0}
             aria-label={`Columnas: ${series.map((s) => s.nombre).join(", ")}`}
             className="block rounded outline-none focus-visible:ring-2 focus-visible:ring-indigo-300"
             style={{ width: "100%", height: "auto" }}
             onKeyDown={(e) => {
               if (e.key === "ArrowRight") { setActivo((c) => Math.min(n - 1, (c ?? -1) + 1)); e.preventDefault(); }
               if (e.key === "ArrowLeft") { setActivo((c) => Math.max(0, (c ?? n) - 1)); e.preventDefault(); }
             }}
             onBlur={() => setActivo(null)} onPointerLeave={() => setActivo(null)}>
          {esc.marcas.map((m) => (
            <g key={m}>
              <line x1={M.izq} x2={W - M.der} y1={sy(m)} y2={sy(m)} stroke={m === 0 ? SERIE.eje : SERIE.rejilla} strokeWidth={1} />
              <text x={M.izq - 6} y={sy(m) + 3.5} textAnchor="end" fontSize="10" className="fill-slate-500 tabular-nums">{formatoEje(m)}</text>
            </g>
          ))}
          {etiquetas.map((et, i) => {
            const x = M.izq + slot * i + (slot - bw) / 2;
            let base = 0;
            const visibles = series.filter((s) => (s.valores[i] ?? 0) > 0);
            return (
              <g key={i} opacity={activo === null || activo === i ? 1 : 0.55}>
                {series.map((s) => {
                  const v = Math.max(0, s.valores[i] ?? 0);
                  if (v <= 0) return null;
                  const y1 = sy(base + v); const y0 = sy(base);
                  const esUltima = visibles[visibles.length - 1] === s;
                  // 2 px de superficie entre segmentos apilados.
                  const h = Math.max(0, y0 - y1 - (base > 0 ? 2 : 0));
                  base += v;
                  return <path key={s.nombre} d={barraRedonda(x, y1, bw, h, 4, esUltima)} fill={s.color} />;
                })}
                {destacarIndice === i && (
                  <text x={x + bw / 2} y={sy(totales[i]) - 5} textAnchor="middle" fontSize="10" fontWeight={600} className="fill-slate-600">
                    {formato(totales[i])}
                  </text>
                )}
                {i % cada === 0 && (
                  <text x={x + bw / 2} y={M.arriba + alto + 15} textAnchor="middle" fontSize="10" className="fill-slate-500">{et}</text>
                )}
                {/* zona de pulso más grande que la barra */}
                <rect x={M.izq + slot * i} y={M.arriba} width={slot} height={alto} fill="transparent"
                      onPointerEnter={() => setActivo(i)} onPointerMove={() => setActivo(i)} />
              </g>
            );
          })}
        </svg>
      )}
      {activo !== null && ancho > 0 && (
        <Tooltip x={M.izq + slot * activo + slot / 2} ancho={W}>
          <div className="mb-1 font-semibold text-slate-700">{etiquetaLarga ? etiquetaLarga(activo) : etiquetas[activo]}</div>
          {series.map((s) => (
            <div key={s.nombre} className="flex items-center gap-2 py-[1px]">
              <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: s.color }} />
              <span className="font-semibold tabular-nums text-slate-900">{s.valores[activo] == null ? "sin dato" : formato(s.valores[activo] as number)}</span>
              <span className="text-slate-500">{s.nombre}</span>
            </div>
          ))}
          {series.length > 1 && (
            <div className="mt-1 border-t border-slate-100 pt-1 text-slate-500">
              Total <b className="tabular-nums text-slate-800">{formato(totales[activo])}</b>
            </div>
          )}
        </Tooltip>
      )}
    </div>
  );
}

// ── Sparkline ───────────────────────────────────────────────────────────────
export function Sparkline({ valores, ancho = 110, alto = 28, color = SERIE.principal, marca }: {
  valores: (number | null)[]; ancho?: number; alto?: number; color?: string; marca?: number | null;
}) {
  const vals = valores.filter((v): v is number => v !== null && Number.isFinite(v));
  if (vals.length < 2) return <span className="inline-block text-[10px] text-slate-300" style={{ width: ancho }}>sin serie</span>;
  const extra = marca !== null && marca !== undefined ? [marca] : [];
  const min = Math.min(...vals, ...extra); const max = Math.max(...vals, ...extra);
  const sx = (i: number) => 2 + (i / (valores.length - 1)) * (ancho - 6);
  const sy = (v: number) => 3 + (alto - 6) - ((v - min) / (max - min || 1)) * (alto - 6);
  const xs = valores.map((_, i) => i);
  const d = trazo(xs, valores, sx, sy);
  let ult = valores.length - 1; while (ult > 0 && (valores[ult] === null || valores[ult] === undefined)) ult--;
  return (
    <svg width={ancho} height={alto} viewBox={`0 0 ${ancho} ${alto}`} aria-hidden className="block">
      {marca !== null && marca !== undefined && (
        <line x1={2} x2={ancho - 4} y1={sy(marca)} y2={sy(marca)} stroke={SERIE.recomendado} strokeWidth={1} strokeDasharray="2 2" opacity={0.8} />
      )}
      <path d={d} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />
      {valores[ult] != null && <circle cx={sx(ult)} cy={sy(valores[ult] as number)} r={2.6} fill={color} />}
    </svg>
  );
}

/** Leyenda: rect para barras, línea para líneas (la leyenda imita la marca). */
export function Leyenda({ items }: { items: { nombre: string; color: string; tipo?: "linea" | "barra" | "guion" | "zona"; valor?: string }[] }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-slate-500">
      {items.map((it) => (
        <span key={it.nombre} className="inline-flex items-center gap-1.5">
          {it.tipo === "barra" ? <span className="h-2.5 w-2.5 rounded-sm" style={{ background: it.color }} />
            : it.tipo === "zona" ? <span className="h-2.5 w-3.5 rounded-sm" style={{ background: it.color, opacity: 0.25 }} />
            : (
              <svg width="16" height="6" aria-hidden>
                <line x1="1" y1="3" x2="15" y2="3" stroke={it.color} strokeWidth="2" strokeLinecap="round" strokeDasharray={it.tipo === "guion" ? "4 3" : undefined} />
              </svg>
            )}
          {it.nombre}
          {it.valor && <b className="font-semibold tabular-nums text-slate-700">{it.valor}</b>}
        </span>
      ))}
    </div>
  );
}

/** La tabla gemela de cada gráfica (el tooltip nunca es la única vía al dato). */
export function TablaGemela({ columnas, filas, titulo = "Ver como tabla" }: { columnas: string[]; filas: (string | number)[][]; titulo?: string }) {
  return (
    <details className="group mt-2">
      <summary className="cursor-pointer select-none text-[11px] font-semibold text-indigo-600 hover:text-indigo-800">{titulo}</summary>
      <div className="mt-2 max-h-64 overflow-auto rounded-lg border border-slate-200 bg-white">
        <table className="w-full text-[11.5px]">
          <thead className="sticky top-0 bg-slate-50">
            <tr>{columnas.map((c) => <th key={c} className="whitespace-nowrap px-2.5 py-1.5 text-left text-[10px] font-semibold uppercase tracking-wider text-slate-500">{c}</th>)}</tr>
          </thead>
          <tbody>
            {filas.map((f, i) => (
              <tr key={i} className="border-t border-slate-50">
                {f.map((c, j) => <td key={j} className="whitespace-nowrap px-2.5 py-1 tabular-nums text-slate-700">{c}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
