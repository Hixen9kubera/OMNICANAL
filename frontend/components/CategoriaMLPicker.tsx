"use client";

import { useEffect, useState } from "react";
import { Search, ChevronRight, Loader2 } from "lucide-react";
import { buscarCategoriasML } from "@/lib/api";
import type { CategoriaMLResult } from "@/lib/types";

interface Props {
  value: string;             // category_id actual (ml_cat_id)
  pathInicial?: string[];    // niveles del postmeta (para mostrar sin buscar)
  onChange: (cat: CategoriaMLResult) => void;
  acento?: string;
}

/**
 * Picker de categoría de Mercado Libre: muestra la categoría actual (breadcrumb +
 * dominio + ID) y permite buscar OTRA por nombre (autocompletado contra
 * /api/crear/categorias-ml). Al elegir, devuelve {category_id, name, path, domain}.
 */
export default function CategoriaMLPicker({ value, pathInicial, onChange, acento = "#4F46E5" }: Props) {
  const [sel, setSel] = useState<CategoriaMLResult | null>(
    value
      ? {
          category_id: value,
          name: pathInicial?.[pathInicial.length - 1] ?? value,
          path: (pathInicial ?? []).join(" > "),
          domain: "",
        }
      : null,
  );
  const [q, setQ] = useState("");
  const [res, setRes] = useState<CategoriaMLResult[]>([]);
  const [buscando, setBuscando] = useState(false);
  const [abierto, setAbierto] = useState(false);

  // Si cambia la categoría inicial (otro SKU), re-siembra la selección.
  useEffect(() => {
    setSel(
      value
        ? { category_id: value, name: pathInicial?.[pathInicial.length - 1] ?? value, path: (pathInicial ?? []).join(" > "), domain: "" }
        : null,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  // Búsqueda con debounce.
  useEffect(() => {
    if (q.trim().length < 2) { setRes([]); return; }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      setBuscando(true);
      buscarCategoriasML(q.trim(), ctrl.signal)
        .then((r) => { setRes(r.resultados); setAbierto(true); })
        .catch(() => setRes([]))
        .finally(() => setBuscando(false));
    }, 350);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [q]);

  function elegir(c: CategoriaMLResult) {
    setSel(c);
    onChange(c);
    setQ("");
    setRes([]);
    setAbierto(false);
  }

  const niveles = sel?.path ? sel.path.split(" > ") : sel ? [sel.name] : [];

  return (
    <div>
      {/* Selección actual */}
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[10px] font-bold uppercase tracking-[0.15em] text-slate-400">Categoría Mercado Libre</span>
          {sel?.category_id && <span className="font-mono text-[10px] text-slate-400">{sel.category_id}</span>}
        </div>
        {sel ? (
          <>
            <div className="flex flex-wrap items-center gap-1 text-sm font-semibold">
              {niveles.map((n, i, arr) => (
                <span key={i} className="flex items-center gap-1">
                  {i > 0 && <ChevronRight size={13} className="text-slate-300" />}
                  <span className={i === arr.length - 1 ? "" : "text-slate-500"} style={i === arr.length - 1 ? { color: acento } : undefined}>{n}</span>
                </span>
              ))}
            </div>
            {sel.domain && <div className="mt-0.5 text-xs font-medium" style={{ color: acento }}>Dominio ML: {sel.domain}</div>}
          </>
        ) : (
          <span className="text-xs text-slate-400">Sin categoría — busca por nombre abajo para poder generar el costo.</span>
        )}
      </div>

      {/* Buscador por nombre */}
      <div className="relative mt-2">
        <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-slate-500">
          <Search size={13} /> Buscar otra categoría por nombre
        </div>
        <div className="relative">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onFocus={() => res.length && setAbierto(true)}
            placeholder="ej. dispensador, taladro, silla…"
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:ring-2"
            style={{ outlineColor: acento }}
          />
          {buscando && <Loader2 size={15} className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-slate-400" />}
        </div>
        {abierto && (res.length > 0 || buscando) && (
          <div className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-lg">
            {!buscando && res.length === 0 && <div className="px-3 py-2 text-xs text-slate-400">Sin resultados.</div>}
            {res.map((c) => (
              <button
                key={c.category_id}
                onClick={() => elegir(c)}
                className="flex w-full flex-col items-start gap-0.5 border-b border-slate-50 px-3 py-2 text-left transition-colors hover:bg-slate-50 last:border-0"
              >
                <div className="flex w-full items-center justify-between gap-2">
                  <span className="font-semibold text-slate-800">{c.name}</span>
                  <span className="shrink-0 font-mono text-[10px] text-slate-400">{c.category_id}</span>
                </div>
                {c.path && <span className="text-xs text-slate-500">{c.path}</span>}
                {c.domain && <span className="text-xs" style={{ color: acento }}>Dominio: {c.domain}</span>}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
