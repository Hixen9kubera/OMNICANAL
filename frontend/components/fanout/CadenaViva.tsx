"use client";

/**
 * CadenaViva — el recorrido del stock: Odoo → stock_watch → Woo → fan-out →
 * canales. Dos capas de movimiento:
 *   · El PULSO, continuo y tenue: dice que la cadena está conectada y a qué
 *     ritmo se mueve cada canal (más rápido cuantos más cambios en 24 h; en rojo
 *     y apagándose donde el canal rechaza; nada donde no hubo actividad). Se
 *     detiene si la página pierde la conexión.
 *   · Los CAMBIOS REALES que llegan en el sondeo: puntos grandes con halo que
 *     recorren la cadena y hacen destellar la tarjeta del canal que los recibe.
 *
 * Los puntos son CSS (keyframes con `--dx/--dy`), no requestAnimationFrame: en
 * pestañas ocultas el rAF se congela y la animación dependería de él (lección
 * de la Red viva).
 */
import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import type { CanalEstado, Columna, Evento, Vivo } from "./tipos";
import { ESTADO_CANAL } from "./tipos";

const W = 1280;
const PASO = 94;           // separación vertical entre tarjetas de canal
const CARD_X = 968;
const CARD_W = 296;
const CARD_H = 84;
const JUNTA_X = 900;
const NODOS = [
  { id: "odoo", x: 16, w: 176 },
  { id: "stock_watch", x: 236, w: 176 },
  { id: "woo", x: 456, w: 176 },
  { id: "fanout", x: 676, w: 164 },
];
const SALIDA = NODOS[0].x + NODOS[0].w;

interface Particula {
  id: number;
  clase: "tronco" | "rama" | "corte";
  x: number;
  y: number;
  dx: number;
  dy: number;
  color: string;
  delay: number;
  dur: number;
  vence: number;
}

function Flecha({ x1, x2, y, color, punta = true, punteada = false }: {
  x1: number; x2: number; y: number; color: string; punta?: boolean; punteada?: boolean;
}) {
  const id = `fo-punta-${color.replace("#", "")}`;
  return (
    <g>
      {punta && (
        <defs>
          <marker id={id} orient="auto" markerWidth="5" markerHeight="5" refX="3.2" refY="2" overflow="visible">
            <path d="M0 0 L4 2 L0 4 Z" fill={color} />
          </marker>
        </defs>
      )}
      <path d={`M ${x1} ${y} L ${x2 - (punta ? 2 : 0)} ${y}`} stroke={color} strokeWidth={2} fill="none"
        strokeLinecap="round" strokeDasharray={punteada ? "6 6" : undefined}
        markerEnd={punta ? `url(#${id})` : undefined} />
    </g>
  );
}

function Nodo({ x, w, top, eyebrow, nombre, l1, l2, l2Aviso }: {
  x: number; w: number; top: number; eyebrow: string; nombre: string; l1: string; l2: string; l2Aviso?: boolean;
}) {
  return (
    <div className="absolute flex items-center rounded-xl border border-slate-300 bg-white px-3.5 py-3"
      style={{ left: x, top, width: w, height: 104 }}>
      <div className="min-w-0 text-xs leading-[17px] text-slate-600">
        <div className="text-[10px] font-semibold uppercase leading-4 tracking-[0.12em] text-slate-500">{eyebrow}</div>
        <div className="text-[15px] font-bold leading-[22px] text-slate-900">{nombre}</div>
        <div className="truncate">{l1}</div>
        <div className={`truncate ${l2Aviso ? "font-semibold text-amber-700" : ""}`}>{l2}</div>
      </div>
    </div>
  );
}

function TarjetaCanal({ c, top, destello }: { c: CanalEstado; top: number; destello?: "ok" | "mal" }) {
  const est = ESTADO_CANAL[c.estado];
  const rechaza = c.estado === "rechaza";
  const co = c.coincidencia;
  return (
    <div className={`absolute flex flex-col justify-center gap-0.5 rounded-xl border px-3.5 py-2 transition-shadow duration-300 ${
      rechaza ? "border-rose-300 bg-rose-50/60" : "border-slate-200 bg-white"} ${
      destello === "ok" ? "ring-4 ring-emerald-300/80" : destello === "mal" ? "ring-4 ring-rose-300/80" : ""}`}
      style={{ left: CARD_X, top, width: CARD_W, height: CARD_H }}>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-bold text-slate-900">{c.nombre}</span>
        <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${est.chip}`}>{est.texto}</span>
      </div>
      {rechaza ? (
        <>
          <div className="text-xs leading-[17px] text-slate-700">
            <b className="text-base text-slate-900">0</b> aceptados desde el {c.ultimo_ok}
          </div>
          <div className="truncate text-[11px] leading-4 text-rose-800">
            {c.racha.toLocaleString("es-MX")} rechazados{c.error_codigo ? ` · ${c.error_codigo}` : ""}
          </div>
        </>
      ) : (
        <>
          <div className="text-xs leading-[17px] text-slate-700">
            <b className="text-base text-slate-900">{c.ok_24h.toLocaleString("es-MX")}</b> cambios en 24 h
            {c.rech_24h ? <span className="text-amber-800"> · {c.rech_24h} rechazados</span> : null}
          </div>
          <div className="truncate text-[11px] leading-4 text-slate-600">
            {co && co.vivas ? `${co.iguales} de ${co.vivas} a la venta iguales a Woo` : `último ${c.ultimo_ok_reciente}`}
          </div>
        </>
      )}
    </div>
  );
}

export default function CadenaViva({ datos, nuevos, tanda, ultimaHora, conectado }: {
  datos: Vivo;
  nuevos: Evento[];
  tanda: number;
  ultimaHora: string | null;
  conectado: boolean;
}) {
  const canales = datos.canales;
  const n = Math.max(1, canales.length);
  const centros = canales.map((_, i) => 14 + i * PASO + CARD_H / 2);
  const fueraTop = 14 + n * PASO;
  const fueraCentro = fueraTop + 28;
  const Y = Math.round(((centros[0] ?? 56) + fueraCentro) / 2);
  const H = fueraTop + 56 + 12;
  const filaTop = Y - 52;

  const yDeColumna = useMemo(() => {
    const m: Record<string, number> = {};
    datos.columnas.forEach((col: Columna) => {
      const i = canales.findIndex((c) => c.canal === col.canal);
      if (i >= 0) m[col.id] = centros[i];
    });
    return m;
  }, [datos.columnas, canales, centros]);

  const [parts, setParts] = useState<Particula[]>([]);
  const [destellos, setDestellos] = useState<Record<string, { tono: "ok" | "mal"; hasta: number }>>({});
  const sec = useRef(0);
  const relojes = useRef<ReturnType<typeof setTimeout>[]>([]);

  // El pulso: una lista FIJA mientras no cambie el ritmo, para que React no
  // reinicie las animaciones en cada sondeo.
  const ritmo = canales.map((c) => `${c.canal}:${c.estado}:${c.ok_24h > 0 ? (c.ok_24h >= 50 ? 3 : c.ok_24h >= 10 ? 2 : 1) : 0}`).join("|");
  const pulso = useMemo(() => {
    const l: { key: string; cls: string; x: number; y: number; dx: number; dy: number; color: string; dur: number; delay: number }[] = [];
    if (!canales.some((c) => c.ok_24h > 0 || c.rech_24h > 0)) return l;
    [0, 1.4, 2.8].forEach((d, i) => l.push({ key: `t${i}`, cls: "fo-amb-tronco", x: SALIDA, y: Y,
      dx: JUNTA_X - SALIDA, dy: 0, color: "#818cf8", dur: 4.2, delay: d }));
    canales.forEach((c, i) => {
      const dy = centros[i] - Y;
      if (c.estado === "rechaza") {
        [0.4, 1.6].forEach((d, j) => l.push({ key: `r${c.canal}${j}`, cls: "fo-amb-corte", x: JUNTA_X, y: Y,
          dx: 30, dy, color: "#f43f5e", dur: 2.4, delay: d }));
      } else if (c.ok_24h > 0) {
        const dur = c.ok_24h >= 50 ? 2.2 : c.ok_24h >= 10 ? 3 : 4;   // más cambios, más rápido
        [0, dur / 2].forEach((d, j) => l.push({ key: `v${c.canal}${j}`, cls: "fo-amb-rama", x: JUNTA_X, y: Y,
          dx: CARD_X - JUNTA_X - 6, dy, color: c.estado === "con_errores" ? "#f59e0b" : "#34d399", dur, delay: d }));
      }
    });
    return l;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ritmo, Y]);

  // Un punto por cambio nuevo: primero el tronco, luego una rama por canal.
  useEffect(() => {
    if (!nuevos.length) return;
    const ahora = Date.now();
    const agregar: Particula[] = [];
    nuevos.slice(0, 6).reverse().forEach((e, i) => {
      const d0 = i * 0.45;
      agregar.push({ id: ++sec.current, clase: "tronco", x: SALIDA, y: Y, dx: JUNTA_X - SALIDA, dy: 0,
        color: "#6366f1", delay: d0, dur: 2.2, vence: ahora + (d0 + 2.4) * 1000 });
      datos.columnas.forEach((col) => {
        const c = e.celdas[col.id];
        const cy = yDeColumna[col.id];
        if (!c || cy === undefined) return;
        if (c.k === "ok") {
          agregar.push({ id: ++sec.current, clase: "rama", x: JUNTA_X, y: Y, dx: CARD_X - JUNTA_X - 6, dy: cy - Y,
            color: "#10b981", delay: d0 + 2.2, dur: 1.2, vence: ahora + (d0 + 3.6) * 1000 });
        } else if (c.k === "mal") {
          agregar.push({ id: ++sec.current, clase: "corte", x: JUNTA_X, y: Y, dx: 30, dy: cy - Y,
            color: "#e11d48", delay: d0 + 2.2, dur: 1.4, vence: ahora + (d0 + 3.8) * 1000 });
        }
        if (c.k === "ok" || c.k === "mal") {
          const tono = c.k;
          // La tarjeta destella cuando el punto llega (o se apaga, si el canal lo rechazó).
          relojes.current.push(setTimeout(() => setDestellos((x) => ({
            ...x, [col.canal]: { tono, hasta: Date.now() + 1400 } })), (d0 + 3.3) * 1000));
        }
      });
    });
    setParts((p) => [...p, ...agregar].slice(-120));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tanda]);

  useEffect(() => {
    const t = setInterval(() => {
      const ahora = Date.now();
      setParts((p) => (p.some((x) => x.vence < ahora) ? p.filter((x) => x.vence >= ahora) : p));
      setDestellos((d) => (Object.values(d).some((x) => x.hasta < ahora)
        ? Object.fromEntries(Object.entries(d).filter(([, x]) => x.hasta >= ahora)) : d));
    }, 500);
    const pendientes = relojes.current;
    return () => { clearInterval(t); pendientes.forEach(clearTimeout); };
  }, []);

  const sw = datos.stock_watch;
  const f = datos.foto;
  const fo = datos.fanout;
  const ml = canales.find((c) => c.estado === "rechaza");
  const mlIdx = ml ? canales.indexOf(ml) : -1;

  return (
    <div className="overflow-x-auto">
      <style>{`
        @keyframes fo-tronco { 0% { transform: translate(0,0); opacity: 0 } 6% { opacity: 1 } 94% { opacity: 1 } 100% { transform: translate(var(--dx), 0); opacity: 0.6 } }
        @keyframes fo-rama { 0% { transform: translate(0,0); opacity: 0 } 6% { opacity: 1 } 40% { transform: translate(0, var(--dy)) } 100% { transform: translate(var(--dx), var(--dy)); opacity: 0.3 } }
        @keyframes fo-corte { 0% { transform: translate(0,0); opacity: 0 } 6% { opacity: 1 } 40% { transform: translate(0, var(--dy)) } 70% { transform: translate(var(--dx), var(--dy)); opacity: 1 } 100% { transform: translate(var(--dx), var(--dy)) scale(2.4); opacity: 0 } }
        @keyframes fo-amb-tronco { 0% { transform: translate(0,0); opacity: 0 } 8% { opacity: .75 } 92% { opacity: .75 } 100% { transform: translate(var(--dx), 0); opacity: 0 } }
        @keyframes fo-amb-rama { 0% { transform: translate(0,0); opacity: 0 } 10% { opacity: .8 } 40% { transform: translate(0, var(--dy)) } 90% { opacity: .8 } 100% { transform: translate(var(--dx), var(--dy)); opacity: 0 } }
        @keyframes fo-amb-corte { 0% { transform: translate(0,0); opacity: 0 } 10% { opacity: .85 } 40% { transform: translate(0, var(--dy)) } 65% { transform: translate(var(--dx), var(--dy)); opacity: .85 } 100% { transform: translate(var(--dx), var(--dy)) scale(2.2); opacity: 0 } }
        .fo-amb { position: absolute; width: 7px; height: 7px; margin: -3.5px 0 0 -3.5px; border-radius: 9999px; pointer-events: none; animation-fill-mode: both; animation-iteration-count: infinite; }
        .fo-dot { position: absolute; width: 12px; height: 12px; margin: -6px 0 0 -6px; border-radius: 9999px; pointer-events: none; animation-fill-mode: both; z-index: 1; }
        @media (prefers-reduced-motion: reduce) { .fo-dot, .fo-amb { display: none } }
      `}</style>
      <div className="relative mx-auto" style={{ width: W, height: H }}>
        <svg className="absolute inset-0" width={W} height={H} aria-hidden="true">
          {NODOS.slice(0, -1).map((nd, i) => (
            <Flecha key={nd.id} x1={nd.x + nd.w} x2={NODOS[i + 1].x} y={Y} color="#94a3b8" />
          ))}
          <Flecha x1={NODOS[3].x + NODOS[3].w} x2={JUNTA_X} y={Y} color="#94a3b8" punta={false} />
          <path d={`M ${JUNTA_X} ${centros[0]} L ${JUNTA_X} ${fueraCentro}`} stroke="#94a3b8" strokeWidth={2} />
          {canales.map((c, i) => (
            <Flecha key={c.canal} x1={JUNTA_X} x2={CARD_X} y={centros[i]} color={ESTADO_CANAL[c.estado].linea} />
          ))}
          <Flecha x1={JUNTA_X} x2={CARD_X} y={fueraCentro} color="#94a3b8" punta={false} punteada />
        </svg>

        {conectado && pulso.map((p) => (
          <span key={p.key} className="fo-amb"
            style={{
              left: p.x, top: p.y, background: p.color,
              animationName: p.cls, animationDuration: `${p.dur}s`, animationDelay: `${p.delay}s`,
              animationTimingFunction: p.cls === "fo-amb-tronco" ? "linear" : "ease-in-out",
              ["--dx" as string]: `${p.dx}px`, ["--dy" as string]: `${p.dy}px`,
            } as CSSProperties} />
        ))}

        {parts.map((p) => (
          <span key={p.id} className="fo-dot"
            style={{
              left: p.x, top: p.y, background: p.color,
              boxShadow: `0 0 0 3px rgba(255,255,255,0.95), 0 0 14px 3px ${p.color}`,
              animation: `fo-${p.clase} ${p.dur}s ${p.clase === "tronco" ? "linear" : "ease-out"} ${p.delay}s both`,
              ["--dx" as string]: `${p.dx}px`, ["--dy" as string]: `${p.dy}px`,
            } as CSSProperties} />
        ))}

        <Nodo x={NODOS[0].x} w={NODOS[0].w} top={filaTop} eyebrow="Maestro" nombre="Odoo"
          l1={`${f.skus_odoo.toLocaleString("es-MX")} SKUs`} l2="menos ventas sin orden" />
        <Nodo x={NODOS[1].x} w={NODOS[1].w} top={filaTop} eyebrow="Cada 20 min" nombre="stock_watch"
          l1={sw.en_memoria && sw.segundos != null ? `Pasada ${sw.ultima} · ${Math.round(sw.segundos)} s` : `Último cambio ${sw.ultima}`}
          l2={`${sw.cambios} cambios a Woo`} />
        <Nodo x={NODOS[2].x} w={NODOS[2].w} top={filaTop} eyebrow="Fuente de los canales" nombre="WooCommerce"
          l1={`${f.skus_woo.toLocaleString("es-MX")} SKUs`}
          l2={f.distintos ? `${f.distintos} distintos de Odoo` : "igual a Odoo"} l2Aviso={!!f.distintos} />
        <Nodo x={NODOS[3].x} w={NODOS[3].w} top={filaTop} eyebrow="Por cambio" nombre="Fan-out"
          l1={`Cola ${fo.cola} · espera ${fo.debounce_s} s`}
          l2={ultimaHora ? `último cambio ${ultimaHora}` : `a ${canales.length} canales`} />

        {canales.map((c, i) => (
          <TarjetaCanal key={c.canal} c={c} top={14 + i * PASO} destello={destellos[c.canal]?.tono} />
        ))}
        {ml && mlIdx >= 0 && ml.error_codigo && (
          <span className="absolute w-10 text-center text-[13px] font-bold leading-4 text-rose-700"
            style={{ left: JUNTA_X + 15, top: centros[mlIdx] - 22 }}>{ml.error_codigo}</span>
        )}
        <div className="absolute flex items-center gap-2 rounded-xl border border-dashed border-slate-400 bg-slate-50 px-3.5 text-xs leading-[17px] text-slate-600"
          style={{ left: CARD_X, top: fueraTop, width: CARD_W, height: 56 }}>
          <span>
            <b className="text-sm text-slate-700">
              {datos.fuera_reparto.map((c) => c.charAt(0).toUpperCase() + c.slice(1)).join(" · ") || "—"}
            </b>
            <br />Fuera del reparto (decisión del 18-ago)
          </span>
        </div>
      </div>
    </div>
  );
}
