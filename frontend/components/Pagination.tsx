"use client";

import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Loader2 } from "lucide-react";
import type { Paginacion } from "@/lib/types";

interface Props {
  pag: Paginacion;
  color: string;
  textoColor: string;
  onPage: (page: number) => void;
  /** true mientras el catálogo se sigue cargando (el total puede crecer) */
  sincronizando?: boolean;
}

function Sincronizando() {
  return (
    <span className="ml-2 inline-flex items-center gap-1.5 align-middle text-xs font-semibold text-indigo-500">
      <Loader2 size={12} className="animate-spin" />
      sincronizando…
    </span>
  );
}

/** Genera la ventana de páginas con elipsis: 1 … 4 5 [6] 7 8 … 120 */
function ventana(actual: number, total: number): (number | "...")[] {
  const out: (number | "...")[] = [];
  const push = (v: number | "...") => out.push(v);
  const span = 1;
  const inicio = Math.max(2, actual - span);
  const fin = Math.min(total - 1, actual + span);

  push(1);
  if (inicio > 2) push("...");
  for (let i = inicio; i <= fin; i++) push(i);
  if (fin < total - 1) push("...");
  if (total > 1) push(total);
  return out;
}

export default function Pagination({ pag, color, textoColor, onPage, sincronizando }: Props) {
  if (pag.total_pages <= 1) {
    return (
      <div className="text-sm text-slate-400">
        {new Intl.NumberFormat("es-MX").format(pag.total)} productos
        {sincronizando && <Sincronizando />}
      </div>
    );
  }

  const paginas = ventana(pag.page, pag.total_pages);
  const btnBase =
    "flex h-9 min-w-9 items-center justify-center rounded-lg border border-slate-200 bg-white px-2 text-sm font-semibold text-slate-600 transition-colors hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40";

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="text-sm text-slate-500">
        Página <span className="font-semibold text-slate-700">{pag.page}</span> de{" "}
        <span className="font-semibold text-slate-700">{pag.total_pages}</span>
        <span className="mx-2 text-slate-300">·</span>
        <span className="font-semibold text-slate-700">
          {new Intl.NumberFormat("es-MX").format(pag.total)}
        </span>{" "}
        productos
        {sincronizando && <Sincronizando />}
      </div>

      <div className="flex items-center gap-1.5">
        <button className={btnBase} disabled={pag.page <= 1} onClick={() => onPage(1)} title="Primera">
          <ChevronsLeft size={16} />
        </button>
        <button
          className={btnBase}
          disabled={!pag.tiene_anterior}
          onClick={() => onPage(pag.page - 1)}
          title="Anterior"
        >
          <ChevronLeft size={16} />
        </button>

        {paginas.map((p, i) =>
          p === "..." ? (
            <span key={`e${i}`} className="px-1 text-slate-400">
              …
            </span>
          ) : (
            <button
              key={p}
              onClick={() => onPage(p)}
              style={p === pag.page ? { backgroundColor: color, color: textoColor, borderColor: color } : undefined}
              className={
                p === pag.page
                  ? "flex h-9 min-w-9 items-center justify-center rounded-lg border px-2 text-sm font-bold shadow-sm"
                  : btnBase
              }
            >
              {p}
            </button>
          ),
        )}

        <button
          className={btnBase}
          disabled={!pag.tiene_siguiente}
          onClick={() => onPage(pag.page + 1)}
          title="Siguiente"
        >
          <ChevronRight size={16} />
        </button>
        <button
          className={btnBase}
          disabled={pag.page >= pag.total_pages}
          onClick={() => onPage(pag.total_pages)}
          title="Última"
        >
          <ChevronsRight size={16} />
        </button>
      </div>
    </div>
  );
}
