"use client";

// Grafica.tsx — La gráfica del detalle, igual que en el artifact: precio de cada
// venta (burbuja = día, número = piezas, altura = precio cobrado), visitas por
// semana y conversión por semana, con el mismo eje de fechas. Las semanas van
// sombreadas alternadas desde el lunes, y a la derecha de «hoy» la proyección
// para agotar el stock al ritmo actual.

import { useRef, useState, type ReactNode } from "react";

import type { PricingMotivo, PricingMotivos } from "@/lib/api";

import { pct1, pesos, type Reloj } from "./formato";
import { SENTIDO, hoyDe, type Fila, type Reglas, type Sentido } from "./reglas";

const COLOR: Record<Sentido, string> = { up: "var(--up)", down: "var(--down)", hold: "var(--hold)", none: "var(--muted)" };

interface Tip {
  x: number;
  y: number;
  dia: string;
  /** El día de la burbuja como "YYYY-MM-DD" (para juntarla con sus motivos). */
  clave: string;
  n: number;
  precio: number;
  cancelada: boolean;
}

const dosDig = (n: number) => String(n).padStart(2, "0");
const claveDia = (d: Date) => `${d.getFullYear()}-${dosDig(d.getMonth() + 1)}-${dosDig(d.getDate())}`;

/**
 * Los motivos de una burbuja: las órdenes de ese día (y ese precio, si lo trae)
 * canceladas —si la burbuja es cancelada— o con devolución —si es una venta—.
 * Se agrupan por motivo con cuántas piezas.
 */
function motivosDe(eventos: PricingMotivo[], tip: Tip): { texto: string; piezas: number; devolucion: boolean }[] {
  const delDia = eventos.filter((e) => e.dia === tip.clave && (tip.cancelada ? e.cancelada : !e.cancelada && e.tipo === "devolucion"));
  const conPrecio = delDia.filter((e) => e.precio == null || Math.abs(e.precio - tip.precio) < 0.5);
  const usar = conPrecio.length ? conPrecio : delDia;
  const grupos = new Map<string, { texto: string; piezas: number; devolucion: boolean }>();
  usar.forEach((e) => {
    const base = e.motivo ?? "ML no dio el motivo";
    const texto =
      e.tipo === "devolucion"
        ? `Devolución: ${base}${e.estado ? ` · ${e.estado}` : ""}`
        : `${base}${e.quien ? ` (la pidió ${e.quien})` : ""}`;
    const g = grupos.get(texto) ?? { texto, piezas: 0, devolucion: e.tipo === "devolucion" };
    g.piezas += e.piezas || 1;
    grupos.set(texto, g);
  });
  return Array.from(grupos.values()).sort((a, b) => b.piezas - a.piezas);
}

/** El porqué en el tooltip de una burbuja cancelada o con devolución. */
function MotivosTip({ motivos, tip }: { motivos?: PricingMotivos | "cargando" | "error" | null; tip: Tip }) {
  if (!motivos) return null;
  if (motivos === "cargando") return tip.cancelada ? <div className="sm motivo">buscando el motivo…</div> : null;
  if (motivos === "error" || !motivos.kubera) return tip.cancelada ? <div className="sm motivo">no se pudo leer el motivo</div> : null;
  const lista = motivosDe(motivos.eventos, tip);
  if (!lista.length) return tip.cancelada ? <div className="sm motivo">sin motivo registrado</div> : null;
  return (
    <div className="motivo">
      {lista.map((g) => (
        <div key={g.texto} className={g.devolucion ? "dev" : ""}>
          {g.piezas > 1 || lista.length > 1 ? <b>{g.piezas} pz · </b> : null}
          {g.texto}
        </div>
      ))}
    </div>
  );
}

export default function Grafica({
  f,
  R,
  rel,
  motivos,
}: {
  f: Fila;
  R: Reglas;
  rel: Reloj;
  motivos?: PricingMotivos | "cargando" | "error" | null;
}) {
  const { p, m, a } = f;
  const caja = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  const { D0, fecha, fFecha } = rel;
  const H = hoyDe(p);

  const W = 960, L = 64, Rm = 96, T1 = 14, H1 = 170, T2 = T1 + H1 + 26, H2 = 70, T3 = T2 + H2 + 26, H3 = 60, HT = T3 + H3 + 28;
  const actividad = [p.vis.findIndex((v) => v > 0), ...p.o.map((o) => o[0])].filter((i) => i >= 0);
  const x0 = Math.max(0, Math.min(H - 27, ...(actividad.length ? actividad : [H - 27])) - 3);
  const finProy = p.stock > 0 && isFinite(m.dias) ? H + m.dias : null;
  const x1 = Math.max(H + 2, Math.min(H + 60, finProy ? finProy + 3 : H + 2));
  const X = (i: number) => L + ((i - x0) / (x1 - x0)) * (W - L - Rm);
  const pag = p.o.filter((o) => o[0] >= x0);
  const ys = [...pag.map((o) => o[1]), m.precio, m.ref, a.precio].filter((v): v is number => !!v && v > 0);
  if (!ys.length) ys.push(1);
  let y0 = Math.min(...ys) * 0.92;
  let y1 = Math.max(...ys) * 1.06;
  if (y1 - y0 < 1) {
    y0 -= 1;
    y1 += 1;
  }
  const Y = (v: number) => T1 + H1 - ((v - y0) / (y1 - y0)) * H1;

  const s: ReactNode[] = [];
  let k = 0;
  const linea = (xa: number, ya: number, xb: number, yb: number, st: string, dash?: string, w = 1) =>
    s.push(<line key={k++} x1={xa} y1={ya} x2={xb} y2={yb} stroke={st} strokeWidth={w} strokeDasharray={dash || undefined} />);
  const texto = (x: number, y: number, t: string, o: { fs?: number; fill?: string; an?: "start" | "middle" | "end"; fw?: number } = {}) =>
    s.push(
      <text key={k++} x={x} y={y} fontSize={o.fs || 11} fill={o.fill || "var(--muted)"} textAnchor={o.an || "start"} fontWeight={o.fw}>
        {t}
      </text>,
    );

  // semanas (lunes) sombreadas alternadas y la proyección
  const semanas: number[] = [];
  for (let i = x0; i <= H; i++) if (fecha(i).getDay() === 1 || i === x0) semanas.push(i);
  semanas.forEach((w, j) => {
    if (j % 2) {
      const fin = semanas[j + 1] ?? H + 1;
      s.push(<rect key={k++} x={X(w)} y={T1} width={X(fin) - X(w)} height={T3 + H3 - T1} fill="var(--panel-2)" />);
    }
  });
  if (finProy) s.push(<rect key={k++} x={X(H)} y={T1} width={X(Math.min(finProy, x1)) - X(H)} height={T3 + H3 - T1} fill="var(--proj)" />);

  // ejes del panel de precio
  [y0 + (y1 - y0) * 0.1, (y0 + y1) / 2, y1 - (y1 - y0) * 0.1].forEach((v) => {
    linea(L, Y(v), W - Rm, Y(v), "var(--line)");
    texto(L - 8, Y(v) + 4, pesos(v), { an: "end" });
  });
  texto(L, T1 - 2, "precio de cada venta", { fill: "var(--fg)", fw: 600 });
  if (p.pub) {
    const ip = Math.round((new Date(p.pub + "T12:00:00").getTime() - D0.getTime()) / 864e5);
    if (ip >= x0 && ip <= H) {
      linea(X(ip), T1, X(ip), T1 + H1, "var(--muted)", "", 1.5);
      texto(X(ip) + 4, T1 + 12, "publicada " + fFecha(ip));
    }
  }
  linea(X(H), T1, X(H), T3 + H3, "var(--muted)", "3 3");
  texto(X(H), T1 - 2, "hoy", { an: "middle" });
  if (finProy && finProy <= x1) {
    linea(X(finProy), T1, X(finProy), T3 + H3, "var(--muted)", "2 3");
    texto(X(finProy), T3 + H3 + 14, "se agota " + fFecha(finProy), { an: "middle", fill: "var(--fg)" });
  }
  if (m.ref) {
    linea(L, Y(m.ref), W - Rm, Y(m.ref), "var(--accent)", "6 4", 1.4);
    texto(W - Rm + 6, Y(m.ref) + 4, "mercado " + pesos(m.ref), { fill: "var(--accent)", fw: 600 });
  }
  linea(X(H) - 30, Y(m.precio), W - Rm, Y(m.precio), "var(--fg)", "", 1.6);
  texto(W - Rm + 6, Y(m.precio) + (m.ref && Math.abs(Y(m.ref) - Y(m.precio)) < 13 ? 15 : 4), "hoy " + pesos(m.precio), {
    fill: "var(--fg)",
    fw: 600,
  });
  if (a.precio) {
    const col = COLOR[SENTIDO[a.k]];
    linea(X(H), Y(a.precio), W - Rm, Y(a.precio), col, "2 3", 1.6);
    const yo = Y(a.precio);
    const ocupado = [m.ref ? Y(m.ref) : null, Y(m.precio)].some((v) => v != null && Math.abs(v - yo) < 13);
    texto(W - Rm + 6, yo + (ocupado ? -8 : 4), "propuesto " + pesos(a.precio), { fill: col, fw: 600 });
  }

  // burbujas: una por día y precio
  const grupos: Record<string, number> = {};
  pag.forEach((o) => {
    const llave = o[0] + "|" + o[1] + "|" + o[3];
    grupos[llave] = (grupos[llave] || 0) + o[2];
  });
  Object.entries(grupos).forEach(([llave, n]) => {
    const [d, pr, cn] = llave.split("|");
    const r = 4 + 3 * Math.sqrt(n);
    const cx = X(+d);
    const cy = Y(+pr);
    const cancelada = cn === "1";
    s.push(
      <g
        key={k++}
        onMouseEnter={(e) => {
          const bx = caja.current?.getBoundingClientRect();
          const gb = e.currentTarget.getBoundingClientRect();
          if (!bx || !caja.current) return;
          const c = caja.current;
          let lx = gb.right - bx.left + c.scrollLeft + 8;
          if (lx + 240 > c.clientWidth + c.scrollLeft) lx = gb.left - bx.left + c.scrollLeft - 248;
          setTip({ x: Math.max(0, lx), y: Math.max(0, gb.top - bx.top - 10), dia: fFecha(+d), clave: claveDia(fecha(+d)), n, precio: +pr, cancelada });
        }}
        onMouseLeave={() => setTip(null)}
      >
        <circle
          cx={cx}
          cy={cy}
          r={r}
          fill={cancelada ? "none" : "var(--accent)"}
          stroke={cancelada ? "var(--muted)" : "var(--panel)"}
          strokeWidth={1.5}
          fillOpacity={0.85}
        />
        {n > 1 && !cancelada && (
          <text x={cx} y={cy + 3.5} fontSize={10} fill="#fff" textAnchor="middle" fontWeight={600}>
            {n}
          </text>
        )}
      </g>,
    );
  });

  // visitas y conversión por semana
  const sem = semanas.map((w, j) => {
    const fin = Math.min(H, (semanas[j + 1] ?? H + 1) - 1);
    let v = 0;
    let u = 0;
    for (let i = w; i <= fin; i++) v += p.vis[i] || 0;
    p.o.forEach((o) => {
      if (!o[3] && o[0] >= w && o[0] <= fin) u += o[2];
    });
    return { w, fin, v, u };
  });
  const vMax = Math.max(1, ...sem.map((z) => z.v));
  texto(L, T2 - 6, "visitas de la semana", { fill: "var(--fg)", fw: 600 });
  linea(L, T2 + H2, W - Rm, T2 + H2, "var(--line)");
  sem.forEach((z) => {
    const xa = X(z.w) + 4;
    const xb = X(z.fin + 1) - 4;
    const hb = (z.v / vMax) * (H2 - 14);
    s.push(<rect key={k++} x={xa} y={T2 + H2 - hb} width={Math.max(2, xb - xa)} height={hb} fill="var(--accent-soft)" rx={2} />);
    texto((xa + xb) / 2, T2 + H2 - hb - 3, z.v.toLocaleString("es-MX"), { an: "middle", fs: 10 });
  });
  const cs = sem.filter((z) => z.v > 0).map((z) => ({ x: (X(z.w) + X(z.fin + 1)) / 2, c: z.u / z.v }));
  const cMax = Math.max(0.02, ...cs.map((z) => z.c));
  texto(L, T3 - 6, "conversión de la semana", { fill: "var(--fg)", fw: 600 });
  linea(L, T3 + H3, W - Rm, T3 + H3, "var(--line)");
  texto(L - 8, T3 + H3 + 4, "0 %", { an: "end" });
  texto(L - 8, T3 + 4 + 8, pct1(cMax).replace(".0", ""), { an: "end" });
  if (R.conv <= cMax) {
    const yc = T3 + 8 + (1 - R.conv / cMax) * (H3 - 8);
    linea(L, yc, W - Rm, yc, "var(--up)", "2 4");
    texto(W - Rm + 6, yc + 4, "umbral " + pct1(R.conv), { fill: "var(--up)", fs: 10 });
  }
  const YC = (c: number) => T3 + 8 + (1 - c / cMax) * (H3 - 8);
  if (cs.length > 1)
    s.push(
      <polyline key={k++} points={cs.map((z) => z.x + "," + YC(z.c)).join(" ")} fill="none" stroke="var(--accent)" strokeWidth={2} />,
    );
  cs.forEach((z) => {
    s.push(<circle key={k++} cx={z.x} cy={YC(z.c)} r={3.5} fill="var(--accent)" />);
    texto(z.x, YC(z.c) - 7, pct1(z.c), { an: "middle", fs: 10, fill: "var(--accent)" });
  });

  // eje de fechas
  sem.filter((_, j) => j % 2 === 0).forEach((z) => texto(X(z.w), HT - 4, fFecha(z.w), { an: "middle", fs: 10.5 }));

  return (
    <div className="grafica" ref={caja}>
      <svg viewBox={`0 0 ${W} ${HT}`} role="img" aria-label={`Ventas, visitas y conversión de ${p.sku ?? p.id}`}>
        {s}
      </svg>
      {tip && (
        <div className="tip" style={{ left: tip.x, top: tip.y }}>
          <div>
            <b>{tip.dia}</b>
            {tip.cancelada ? " · cancelada" : ""}
          </div>
          <div>
            {tip.n} pieza{tip.n > 1 ? "s" : ""} a <b>{pesos(tip.precio)}</b>
          </div>
          <div>{pesos(tip.n * tip.precio)} de venta</div>
          {m.ref ? (
            <div className="sm">
              {tip.precio < m.ref ? "abajo" : "arriba"} del precio de mercado ({pesos(m.ref)})
            </div>
          ) : null}
          <MotivosTip motivos={motivos} tip={tip} />
        </div>
      )}
    </div>
  );
}
