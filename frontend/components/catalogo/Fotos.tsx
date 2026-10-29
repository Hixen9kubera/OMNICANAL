"use client";

// Fotos.tsx — Las fotos del CATÁLOGO salen de HOJAS (`f0`, `f1`…: cuadrícula de 10 × 10 fotos de 192 px), como en
// PRICING. Cada hoja se pide UNA vez, con la sesión, y se usa de fondo recortado. La foto de cada fila es la de su
// renglón del packing list; la de Odoo solo donde no hay renglón con foto (`fo`). Clic → la misma foto en grande.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { ImageOff, X } from "lucide-react";

import { catalogoHoja } from "@/lib/api";
import type { Fila } from "./logica";

interface Hojas {
  url: (nombre: string) => string | null;
  pedir: (nombre: string) => void;
  lado: number;
  cols: number;
  porHoja: number;
  abrir: (r: Fila) => void;
}

const Contexto = createContext<Hojas | null>(null);

export function ProveedorFotos({ version, lado, cols, porHoja, children }: {
  version: string; lado: number; cols: number; porHoja: number; children: React.ReactNode;
}) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [lupa, setLupa] = useState<Fila | null>(null);
  const pedidas = useRef(new Set<string>());
  const vivas = useRef<string[]>([]);

  const pedir = useCallback((nombre: string) => {
    if (pedidas.current.has(nombre)) return;
    pedidas.current.add(nombre);
    catalogoHoja(nombre, version)
      .then((u) => {
        vivas.current.push(u);
        setUrls((prev) => ({ ...prev, [nombre]: u }));
      })
      .catch(() => {
        // Sin hoja no se rompe nada: queda el cuadro vacío.
      });
  }, [version]);

  useEffect(() => () => vivas.current.forEach((u) => URL.revokeObjectURL(u)), []);

  const url = useCallback((nombre: string) => urls[nombre] ?? null, [urls]);
  return (
    <Contexto.Provider value={{ url, pedir, lado, cols, porHoja, abrir: setLupa }}>
      {children}
      {lupa && <Lupa r={lupa} onCerrar={() => setLupa(null)} />}
    </Contexto.Provider>
  );
}

function useRecorte(f: number | undefined, px: number) {
  const ctx = useContext(Contexto);
  const nombre = f == null || !ctx ? null : `f${Math.floor(f / ctx.porHoja)}`;
  const pedir = ctx?.pedir;
  useEffect(() => {
    if (nombre && pedir) pedir(nombre);
  }, [nombre, pedir]);
  const url = nombre && ctx ? ctx.url(nombre) : null;
  if (f == null || !ctx || !url) return null;
  const pos = f % ctx.porHoja;
  const k = px / ctx.lado;
  return {
    backgroundImage: `url(${url})`,
    backgroundSize: `${ctx.cols * ctx.lado * k}px auto`,
    backgroundPosition: `-${(pos % ctx.cols) * ctx.lado * k}px -${Math.floor(pos / ctx.cols) * ctx.lado * k}px`,
    backgroundRepeat: "no-repeat",
  };
}

/** La foto de una fila. Con `zoom`, clic (o Enter) la abre en grande. */
export function Foto({ r, px, zoom = true }: { r: Fila; px: number; zoom?: boolean }) {
  const ctx = useContext(Contexto);
  const estilo = useRecorte(r.f, px);
  if (r.f == null) {
    return (
      <span className="flex shrink-0 items-center justify-center rounded-lg border border-dashed border-slate-200 bg-slate-50 text-slate-300"
        style={{ width: px, height: px }} title="Sin foto">
        <ImageOff size={Math.max(12, px / 3)} aria-hidden />
      </span>
    );
  }
  const base = "block shrink-0 rounded-lg border border-slate-200 bg-white";
  if (!estilo) return <span className={`${base} animate-pulse bg-slate-100`} style={{ width: px, height: px }} />;
  if (!zoom) return <span className={base} role="img" aria-label={`Foto de ${r.s}`} style={{ width: px, height: px, ...estilo }} />;
  return (
    <button type="button" onClick={() => ctx?.abrir(r)}
      className={`${base} cursor-zoom-in transition hover:ring-2 hover:ring-indigo-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500`}
      title="Clic para verla en grande" aria-label={`Foto de ${r.sin ? "producto sin SKU" : r.s}: clic para verla en grande`}
      style={{ width: px, height: px, ...estilo }} />
  );
}

function Lupa({ r, onCerrar }: { r: Fila; onCerrar: () => void }) {
  const [ancho, setAncho] = useState(384);
  useEffect(() => {
    setAncho(Math.min(384, window.innerWidth - 64));
    const tecla = (e: KeyboardEvent) => { if (e.key === "Escape") onCerrar(); };
    document.addEventListener("keydown", tecla);
    return () => document.removeEventListener("keydown", tecla);
  }, [onCerrar]);
  const estilo = useRecorte(r.f, ancho);
  return (
    <div role="dialog" aria-modal="true" aria-label="Foto del producto en grande"
      className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-900/70 p-4 backdrop-blur-sm" onClick={onCerrar}>
      <div className="flex max-w-full flex-col gap-3 rounded-2xl bg-white p-4 shadow-card-hover" onClick={(e) => e.stopPropagation()}>
        <div className="rounded-xl border border-slate-200 bg-white" style={{ width: ancho, height: ancho, ...(estilo ?? {}) }} />
        <div className="flex items-start justify-between gap-3" style={{ maxWidth: ancho }}>
          <div className="min-w-0 text-sm">
            <div className="font-mono text-xs font-bold text-slate-900">{r.sin ? "Sin SKU" : r.s}</div>
            <div className="text-slate-700">{r.t}</div>
            <div className="mt-1 text-xs text-slate-500">
              {r.fo === "pl" ? "Foto de su renglón en el packing list." : "Foto de Odoo: la fila no tiene renglón con foto."}
            </div>
          </div>
          <button type="button" onClick={onCerrar} aria-label="Cerrar"
            className="rounded-lg border border-slate-200 p-1.5 text-slate-500 hover:bg-slate-50 hover:text-slate-800">
            <X size={16} aria-hidden />
          </button>
        </div>
      </div>
    </div>
  );
}
