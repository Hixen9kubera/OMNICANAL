"use client";

// Celdas.tsx — Las piezas que se repiten entre pestañas del CATÁLOGO: el trío mín · media · máx, la celda de
// precio por pieza, la de valor, el estado de una fila y las etiquetas. Port del artifact (`celdaPrecio`,
// `celdaValor`, `etEstado`).

import type { ReactNode } from "react";
import { FML, NIVEL, type Estimado, type Fila, type Precio, etiquetaEstimado, fm, fm0, nf } from "./logica";

export const Mini = ({ children, className = "" }: { children: ReactNode; className?: string }) => (
  <div className={`text-[11px] leading-snug text-slate-500 ${className}`}>{children}</div>
);

export function Etiqueta({ children, tono = "slate" }: { children: ReactNode; tono?: "slate" | "amber" | "rose" | "emerald" | "indigo" }) {
  const c = {
    slate: "bg-slate-100 text-slate-600 ring-slate-200", amber: "bg-amber-50 text-amber-800 ring-amber-200",
    rose: "bg-rose-600 text-white ring-rose-600", emerald: "bg-emerald-50 text-emerald-700 ring-emerald-200",
    indigo: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  }[tono];
  return <span className={`inline-flex items-center whitespace-nowrap rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold ring-1 ${c}`}>{children}</span>;
}

export const SoldOut = () => <Etiqueta tono="rose">SOLD OUT</Etiqueta>;

export function Estado({ r }: { r: Fila }) {
  if (r.nv) return <span className="flex flex-wrap gap-1">{r.sin && <Etiqueta tono="amber">sin SKU</Etiqueta>}<Etiqueta>insumo, no se valúa</Etiqueta></span>;
  if (r.sin) return <Etiqueta tono="amber">sin SKU</Etiqueta>;
  if (r.so) return <SoldOut />;
  if (r.pe != null) return <Etiqueta tono="amber">renglón no ubicado</Etiqueta>;
  if (r.pc == null) return <Etiqueta>sin packing list</Etiqueta>;
  return null;
}

export function Tri({ a, b, c }: { a: string; b: string; c: string }) {
  return (
    <div className="grid grid-cols-3 items-baseline gap-1.5 text-right tabular-nums">
      <span className="text-[11.5px] text-slate-500">{a}</span>
      <span className="text-[12.5px] font-bold text-slate-900">{b}</span>
      <span className="text-[11.5px] text-slate-500">{c}</span>
    </div>
  );
}

export const TriCab = () => (
  <div className="mt-0.5 grid grid-cols-3 gap-1.5 text-right text-[9.5px] font-medium normal-case tracking-normal opacity-80">
    <span>mín</span><span>media</span><span>máx</span>
  </div>
);

/** Precio por pieza de un canal: mínimo, media y máximo, y de qué sale. */
export function CeldaPrecio({ p, firme, est }: { p?: Precio; firme: boolean; est?: Estimado }) {
  if (p && p.o === "e") {
    const que = p.cl === "r" ? "rango revisado a mano" : p.cl === "f" ? `mismo producto${p.nv === "x" ? " (del modelo)" : ""}`
      : p.cl === "b" ? `banda${p.cg ? ` «${p.cg}»` : ""}` : p.cl === "k" ? `mercado sin marcas${p.cg ? ` «${p.cg}»` : ""}`
      : "sin publicaciones: nuestro precio";
    return (
      <>
        {p.lo != null ? <Tri a={fm0(p.lo)} b={fm(p.me)} c={fm0(p.hi)} /> : <Tri a="—" b={fm(p.m)} c="—" />}
        <Mini>{que}{p.n ? ` · ${p.n} publ.` : ""}{p.pk ? ` · paquete de ${p.pk}` : ""}</Mini>
      </>
    );
  }
  if (firme && p) {
    return (<><Tri a={fm0(p.lo)} b={fm(p.m)} c={fm0(p.hi)} /><Mini>{p.n} publicaciones{p.pk ? ` · ÷${p.pk}` : ""}{p.o === "g" ? " · del modelo" : ""}</Mini></>);
  }
  if (!est) return (<><Tri a="—" b="—" c="—" />{p && <Mini>indicio: {fm0(p.m)}</Mini>}</>);
  const propio = est[1] === "t" || est[1] === "i";
  return (
    <>
      <Tri a={propio && p ? fm0(p.lo) : "—"} b={fm(est[0])} c={propio && p ? fm0(p.hi) : "—"} />
      <Mini>estimado: {etiquetaEstimado(est)}{est[4] ? " · topado" : ""}</Mini>
      {p && !propio && <Mini>indicio: {fm0(p.m)} ({p.dz ? `${p.dz}× su precio de ML` : p.o === "c" ? "catálogo" : p.x ? "otro paquete" : p.n === 1 ? "1 publicación" : "sin SKU"})</Mini>}
    </>
  );
}

/** Valor de lo que queda: piezas × precio por pieza, y qué precio es. */
export function CeldaValor({ medido, estimado, r, precio, p, conReferencia }: {
  medido: number | null; estimado: number | null; r: Fila; precio: number | null; p?: Precio; conReferencia?: boolean;
}) {
  const cuenta = precio != null && r._q != null ? <Mini>{nf(r._q)} pzas × {fm(precio)}</Mini> : null;
  const deML = p && p.o === "e" && medido != null
    ? <Mini>{FML[p.cl || ""]}{p.tp ? " · topado" : ""}{p.sr ? " · sin revisar" : ""}{p.v3?.m ? " · revisado a mano" : ""}{p.v3?.rv ? " · para revisar" : ""}</Mini>
    : null;
  if (medido != null) return <><div className="text-right text-[13px] font-bold tabular-nums text-slate-900">{fm0(medido)}</div>{cuenta}{deML}</>;
  if (estimado != null) return <><div className="text-right text-[13px] font-bold tabular-nums text-slate-900">{fm0(estimado)}</div>{cuenta}<Mini>estimado</Mini></>;
  if (r.nv) {
    return (
      <>
        <div className="text-right text-slate-300">—</div>
        <Mini>insumo: no se valúa</Mini>
        {r.mr && conReferencia && <Mini>ref. por pieza {fm(r.mr.lo)} · {fm(r.mr.me)} · {fm(r.mr.hi)}</Mini>}
      </>
    );
  }
  return <div className="text-right text-slate-300">—</div>;
}

export const nivelDe = (e: Estimado) => NIVEL[e[3] || ""] || "su subcategoría";
