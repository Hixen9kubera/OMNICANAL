"use client";

import { useEffect, useState } from "react";
import { Search, ChevronRight, Loader2 } from "lucide-react";
import { buscarCategoriasML, obtenerCategoriaML } from "@/lib/api";
import type { CategoriaMLResult } from "@/lib/types";

interface Props {
  value: string;             // category_id actual (ml_cat_id)
  pathInicial?: string[];    // niveles del postmeta (para mostrar sin buscar)
  onChange: (cat: CategoriaMLResult) => void;
  acento?: string;
}

/**
 * Picker de categoría de Mercado Libre: muestra la categoría actual (breadcrumb +
 * dominio + ID) y permite buscar OTRA por texto o por ID (autocompletado contra
 * /api/crear/categorias-ml). Al elegir, devuelve {category_id, name, path, domain}.
 *
 * Por texto, el backend busca en DOS lados: el árbol completo de ML (0059: la
 * categoría por su NOMBRE, "lavabos") y el predictor de ML (la categoría a partir
 * del TÍTULO del producto). Las ramas —donde ML no deja publicar— se muestran
 * pero no se pueden elegir.
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
  // La última búsqueda TERMINÓ sin nada. Sin esto el aviso de "sin resultados"
  // no se veía nunca: vivía dentro de un bloque que solo se pinta con resultados.
  const [vacio, setVacio] = useState(false);

  // Si cambia la categoría inicial (otro SKU), re-siembra la selección.
  useEffect(() => {
    setSel(
      value
        ? { category_id: value, name: pathInicial?.[pathInicial.length - 1] ?? value, path: (pathInicial ?? []).join(" > "), domain: "" }
        : null,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  // Hay ID pero sin breadcrumb (categoría asignada por un proceso viejo, sin
  // niveles en el postmeta) → completar el texto con un lookup por ID.
  useEffect(() => {
    if (!value || pathInicial?.length) return;
    const ctrl = new AbortController();
    obtenerCategoriaML(value, ctrl.signal)
      .then((c) => setSel((s) => (s && s.category_id === value ? { ...s, ...c } : s)))
      .catch(() => {});
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, pathInicial?.length]);

  // Si lo tecleado TRAE UN ID (MLM31513, o una URL de ML que lo contenga) se
  // resuelve por lookup directo en vez de buscar por nombre.
  //
  // Motivo (medido 25-ago): el buscador por nombre usa `domain_discovery`, que
  // es un PREDICTOR de categoría, no un índice del árbol. Hay categorías
  // perfectamente publicables que NUNCA sugiere. Caso real: MLM31513
  // "Hogar, Muebles y Jardín > Baños > Lavabos para Baño" — hoja, status
  // enabled, listing_allowed true, 15,377 publicaciones — y aun así buscar
  // "lavabo", "lavamanos" u "ovalín" solo devuelve MLM189323 (Construcción) y
  // MLM455948 (Accesorios Náuticos). Sin esta salida esas categorías son
  // INALCANZABLES desde el panel.
  const idSuelto = q.trim().match(/\bMLM\d{3,}\b/i)?.[0]?.toUpperCase() ?? "";

  // Búsqueda con debounce. Un ID pegado que resulta ser una RAMA (breadcrumb
  // cortado, "Equipos de Cosmetología >") trae debajo sus subcategorías
  // publicables: la rama se ve, pero se elige una de ellas.
  useEffect(() => {
    setVacio(false);
    if (!idSuelto && q.trim().length < 2) { setRes([]); return; }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      setBuscando(true);
      const pedido = idSuelto
        ? obtenerCategoriaML(idSuelto, ctrl.signal).then((c) => [c, ...(c.subcategorias ?? [])])
        : buscarCategoriasML(q.trim(), ctrl.signal).then((r) => r.resultados);
      pedido
        .then((r) => { setRes(r); setVacio(r.length === 0); setAbierto(true); })
        .catch(() => {
          if (ctrl.signal.aborted) return; // otra tecla ya lanzó la siguiente búsqueda
          setRes([]);
          setVacio(true);
          setAbierto(true);
        })
        .finally(() => setBuscando(false));
    }, 350);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [q, idSuelto]);

  function elegir(c: CategoriaMLResult) {
    if (c.publicable === false) return; // una rama: ML la rechazaría al publicar
    const elegida = { category_id: c.category_id, name: c.name, path: c.path, domain: c.domain };
    setSel(elegida);
    onChange(elegida);
    setQ("");
    setRes([]);
    setAbierto(false);
  }

  // Una rama con más subcategorías de las que caben en la lista.
  const rama = res.find((c) => c.publicable === false);
  const subsFuera = rama ? (rama.subcategorias_total ?? 0) - (rama.subcategorias?.length ?? 0) : 0;

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
          <Search size={13} /> Buscar otra categoría por nombre o pegar su ID
        </div>
        <div className="relative">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onFocus={() => res.length && setAbierto(true)}
            placeholder="ej. lavabos, bocinas, soportes para vehículos… o MLM31513"
            className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-700 outline-none focus:ring-2"
            style={{ outlineColor: acento }}
          />
          {buscando && <Loader2 size={15} className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-slate-400" />}
        </div>
        {abierto && (res.length > 0 || buscando || vacio) && (
          <div className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-lg">
            {!buscando && res.length === 0 && (
              <div className="px-3 py-2 text-xs text-slate-400">
                {idSuelto
                  ? `Mercado Libre no reconoce ${idSuelto}.`
                  : "Sin resultados. Prueba con el nombre de la categoría (ej. «bocinas», «otros cosmetología») o pega su ID."}
              </div>
            )}
            {res.map((c) => {
              const esRama = c.publicable === false;
              return (
                <button
                  key={c.category_id}
                  onClick={() => elegir(c)}
                  disabled={esRama}
                  className={`flex w-full flex-col items-start gap-0.5 border-b border-slate-50 px-3 py-2 text-left transition-colors last:border-0 ${
                    esRama ? "cursor-not-allowed bg-amber-50/60" : "hover:bg-slate-50"
                  }`}
                >
                  <div className="flex w-full items-center justify-between gap-2">
                    <span className={`font-semibold ${esRama ? "text-slate-500" : "text-slate-800"}`}>{c.name}</span>
                    <span className="flex shrink-0 items-center gap-1.5">
                      {c.sugerida && (
                        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">
                          Sugerida por ML
                        </span>
                      )}
                      <span className="font-mono text-[10px] text-slate-400">{c.category_id}</span>
                    </span>
                  </div>
                  {c.path && <span className="text-xs text-slate-500">{c.path}</span>}
                  {c.domain && <span className="text-xs" style={{ color: acento }}>Dominio: {c.domain}</span>}
                  {esRama && (
                    <span className="text-xs font-medium text-amber-700">
                      {c.subcategorias?.length
                        ? `Mercado Libre no acepta publicaciones aquí. Elige una de sus ${c.subcategorias_total ?? c.subcategorias.length} subcategorías:`
                        : "Mercado Libre no acepta publicaciones aquí. Busca una de sus subcategorías por nombre."}
                    </span>
                  )}
                </button>
              );
            })}
            {subsFuera > 0 && (
              <div className="px-3 py-2 text-xs text-slate-400">…y {subsFuera} más. Escribe su nombre para acotar.</div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
